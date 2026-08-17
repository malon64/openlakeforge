"""Consumer install: fetch, verify, and apply a tagged OpenLakeForge release
with no repository clone, then prove the running cluster matches the
release manifest exactly.

Reuses the same Terraform roots, Helm values, and phase scripts as
`make local-up` (ADR 0024): `scripts/local/stack/platform-up.sh` and
`deploy-artifacts.sh` are driven with image references and layer toggles
resolved from `component-manifest.json`, never from repository state.

Image digests are proven, not chart-pinned: `.github/workflows/release.yml`
retags every pushed digest with the plain catalog version
(`docker buildx imagetools create -t "<repo>:<version>" "<repo>@<digest>"`),
so `<repo>:<version>` always resolves to exactly the digest recorded in the
manifest's `resolved_images`. `run_verify` reads back each running
container's *resolved* imageID (`repo@sha256:...`) and compares it against
that digest -- the proof that matters, independent of how the image was
pulled.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from olf import log

DEFAULT_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
DEFAULT_REPO_SLUG = "malon64/openlakeforge"

# Catalog image names that must be running regardless of profile, and those
# gated behind the optional governance/analytics layers (ADR: layers.py).
_PROFILE_CATALOG_IMAGES = {
    "always": ("postgres", "seaweedfs", "polaris", "trino"),
    "governance": ("opensearch",),
    "analytics": ("superset_redis",),
}
# manifest["resolved_images"] entries (the two first-party images) required
# per profile.
_PROFILE_RESOLVED_IMAGES = {
    "always": ("project-code",),
    "analytics": ("superset",),
}

# Every release/component-catalog.yaml image intentionally absent from
# _PROFILE_CATALOG_IMAGES, with why. run_verify only observes RUNNING pods
# (pod_images_from_payload excludes both non-Running phases and
# initContainers entirely -- see its docstring), so an image backing a
# one-off bootstrap Job the scheduler deletes on completion, a Dockerfile
# FROM base baked into a first-party image at build time, or one that never
# runs inside the cluster's pods at all can never be observed running even
# on a healthy install -- requiring it in _PROFILE_CATALOG_IMAGES would
# report a permanent false MISSING.
#
# test_expected_images_accounts_for_every_real_catalog_image (test_install.py)
# asserts every name in the real catalog is covered by EITHER this dict or
# _PROFILE_CATALOG_IMAGES: a new catalog image landing in neither -- exactly
# what happened to polaris_admin_tool when it was added upstream without a
# matching update here -- fails that test instead of silently never being
# checked by `olf install verify`.
_INTENTIONALLY_EXCLUDED_CATALOG_IMAGES = {
    "k8s_bootstrap": "Polaris/OpenMetadata bootstrap Job image; on-demand, TTL-deleted after completion.",
    "openmetadata_ingestion": (
        "infra/helm/values/local/openmetadata.yaml's pipelineServiceClientConfig.k8s.ingestionImage -- "
        "only used for on-demand ingestion Jobs OpenMetadata's scheduler creates and later deletes (TTL)."
    ),
    "polaris_admin_tool": (
        "infra/terraform/modules/catalog/polaris's metastore_bootstrap_job_image -- backs the one-off "
        "Polaris metastore bootstrap Job, not a long-running Deployment."
    ),
    "superset_init": (
        "infra/helm/values/local/superset.yaml's initImage -- confirmed against the pinned Superset "
        "chart (0.15.5) that it backs ONLY wait-for-postgres/wait-for-postgres-redis initContainers, "
        "never a regular container; pod_images_from_payload excludes initContainers entirely, so it can "
        "never be observed running at all."
    ),
    "floe": (
        "Never deployed as a cluster pod at all -- olf install run's Phase 2 runs it as an ephemeral "
        "`docker run` on the installer's own host (see scripts/artifacts/floe-manifest.sh), not inside "
        "the target cluster."
    ),
    "project_code_base": "Dockerfile FROM base baked into the built project-code image; never deployed standalone.",
    "superset_base": "Dockerfile FROM base baked into the built superset image; never deployed standalone.",
}

_PROFILE_ENV = {
    "slim": {"ENABLE_GOVERNANCE": "false", "ENABLE_ANALYTICS": "false"},
    "full": {"ENABLE_GOVERNANCE": "true", "ENABLE_ANALYTICS": "true"},
}


class InstallError(RuntimeError):
    """Raised when fetching, verifying, or applying a release fails."""


@dataclass(frozen=True)
class ReleaseAssets:
    directory: Path

    def path(self, name: str) -> Path:
        return self.directory / name


# --- Fetch -------------------------------------------------------------


def fetch_release_assets(tag: str, *, repo_slug: str, dest_dir: Path, session: Any = None) -> ReleaseAssets:
    """Download every published asset for `tag` via the public GitHub Releases
    API into `dest_dir`. Reads only public release metadata and asset URLs --
    no credentials required, mirroring the curl fallback in
    scripts/release/verify-install.sh.
    """
    http = session or requests
    dest_dir.mkdir(parents=True, exist_ok=True)
    api_url = f"https://api.github.com/repos/{repo_slug}/releases/tags/{tag}"
    response = http.get(api_url, timeout=30)
    if response.status_code != 200:
        raise InstallError(f"could not fetch release metadata for {tag} from {api_url}: HTTP {response.status_code}")
    assets = (response.json() or {}).get("assets") or []
    if not assets:
        raise InstallError(f"release {tag} has no published assets")
    for asset in assets:
        name = asset["name"]
        url = asset["browser_download_url"]
        log.step(f"Downloading {name}...")
        asset_response = http.get(url, timeout=180)
        if asset_response.status_code != 200:
            raise InstallError(f"failed to download asset {name!r} from {url}: HTTP {asset_response.status_code}")
        (dest_dir / name).write_bytes(asset_response.content)
    return ReleaseAssets(dest_dir)


# --- Checksums (pure) ----------------------------------------------------


def parse_checksums(text: str) -> list[tuple[str, str]]:
    """Parse a `sha256sum -c`-compatible checksums.txt into (digest, path) pairs."""
    entries: list[tuple[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        digest, separator, path = line.partition("  ")
        if not separator or not digest or not path:
            raise InstallError(f"malformed checksums.txt line: {line!r}")
        entries.append((digest, path))
    return entries


def verify_checksums(directory: Path, checksums_text: str) -> None:
    """Verify every file named in `checksums_text` against its recorded sha256."""
    problems: list[str] = []
    for digest, name in parse_checksums(checksums_text):
        path = directory / name
        if not path.is_file():
            problems.append(f"{name}: missing")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != digest:
            problems.append(f"{name}: checksum mismatch (expected {digest}, got {actual})")
    if problems:
        raise InstallError("checksum verification failed: " + "; ".join(problems))


# --- Cosign (shells out; identity/verification logic mirrors
# scripts/release/verify-install.sh so both paths prove the same thing) ----


def certificate_identity_regexp(repo_slug: str, tag: str) -> str:
    reference = f"https://github.com/{repo_slug}/.github/workflows/release.yml@refs/tags/{tag}"
    return f"^{re.escape(reference)}$"


def cosign_verify_blob(
    blob_path: Path,
    bundle_path: Path,
    *,
    identity_regexp: str,
    oidc_issuer: str = DEFAULT_OIDC_ISSUER,
    run: Any = subprocess.run,
) -> None:
    result = run(
        [
            "cosign",
            "verify-blob",
            str(blob_path),
            "--bundle",
            str(bundle_path),
            "--certificate-identity-regexp",
            identity_regexp,
            "--certificate-oidc-issuer",
            oidc_issuer,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise InstallError(f"cosign verify-blob failed for {blob_path.name}: {(result.stderr or '').strip()}")


def cosign_verify_image(
    reference: str,
    *,
    identity_regexp: str,
    oidc_issuer: str = DEFAULT_OIDC_ISSUER,
    run: Any = subprocess.run,
) -> None:
    result = run(
        [
            "cosign",
            "verify",
            reference,
            "--certificate-identity-regexp",
            identity_regexp,
            "--certificate-oidc-issuer",
            oidc_issuer,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise InstallError(f"cosign verify failed for {reference}: {(result.stderr or '').strip()}")


# --- Bundle unpack ---------------------------------------------------------


# Never deleted by _clear_stale_bundle_content, even though it is not part
# of any install bundle: Terraform's own provider/module plugin cache,
# written into the extracted tree by `terraform init`. Purely a performance
# optimization (avoids re-downloading providers on every run) -- unlike
# Terraform's actual STATE, which run_install keeps outside this tree
# entirely via an explicit backend path (see TERRAFORM_STATE_PATH), .terraform
# holding stale/mismatched provider selections is self-healing (`terraform
# init` just re-resolves them), never a correctness or data-loss risk, so it
# is fine for this to be the one thing that survives a wipe.
_PRESERVED_ACROSS_UPGRADES_DIR = ".terraform"


def _clear_stale_bundle_content(dest_dir: Path) -> None:
    """Remove every previously-extracted file under `dest_dir` except the
    Terraform provider cache, so the extraction that follows starts from
    exactly the new release's tracked files.

    Without this, a file removed or renamed between releases lingers on
    disk from the prior extraction and keeps being loaded -- notably, a
    stale `.tf` file left behind by an upgrade stays loaded by `terraform
    init`/`apply` alongside the new release's roots, which can preserve
    obsolete resources or collide with a renamed one.
    """
    for path in dest_dir.rglob("*"):
        if not path.is_file():
            continue
        if _PRESERVED_ACROSS_UPGRADES_DIR in path.parts:
            continue
        path.unlink()


def unpack_install_bundle(bundle_path: Path, dest_dir: Path) -> Path:
    """Extract install-bundle.tar.gz's tracked content directly into `dest_dir`,
    stripping the archive's own `openlakeforge-<version>/` top-level prefix.
    Always returns `dest_dir` itself.

    The extraction target is deliberately version-*independent*: installing
    a different tag into the same --work-dir overwrites tracked file content
    in place at the SAME path, rather than landing under a new
    version-named subdirectory. Terraform's STATE lives entirely outside this
    tree (run_install configures an explicit backend path under
    `<work-dir>/state/`, never inside `<work-dir>/bundle/` -- see
    TERRAFORM_STATE_PATH), so a wipe-and-re-extract here can never touch it;
    the version-independent path exists so the Terraform *provider cache*
    (`.terraform/`, preserved -- see `_clear_stale_bundle_content`) doesn't
    need re-downloading on every run either.

    Every previously-extracted tracked file is removed before the new
    archive is extracted (provider cache excepted -- see
    `_clear_stale_bundle_content`), so a file the new release removed or
    renamed does not linger and keep being loaded by Terraform.
    """
    with tarfile.open(bundle_path) as archive:
        members = archive.getmembers()
        top_level_names = {member.name.split("/", 1)[0] for member in members if member.name}
        if len(top_level_names) != 1:
            raise InstallError(
                f"expected exactly one top-level directory in {bundle_path.name}, found {len(top_level_names)}"
            )
        prefix = next(iter(top_level_names)) + "/"

        dest_dir.mkdir(parents=True, exist_ok=True)
        _clear_stale_bundle_content(dest_dir)
        for member in members:
            if not member.name.startswith(prefix):
                continue  # the top-level directory entry itself, if present
            member.name = member.name[len(prefix) :]
            if not member.name:
                continue
            archive.extract(member, dest_dir, filter="data")

    return dest_dir


_PERSISTED_STORAGE_OVERRIDE_NAME = "storage-values-override.yaml"


def resolve_kubeconfig_path(kubeconfig_path: str | None) -> str:
    """Resolve --kubeconfig (or the ambient KUBECONFIG) to a single absolute
    path.

    Kubernetes defines KUBECONFIG as potentially an os.pathsep-separated
    LIST of files that kubectl merges into one logical view at runtime (see
    https://kubernetes.io/docs/concepts/configuration/organize-cluster-access-kubeconfig/#the-kubeconfig-environment-variable).
    Terraform's kubernetes/helm providers take only a single config_path,
    though, and every other consumer downstream of this function (the
    phase scripts' KUBECONFIG_PATH/KUBECONFIG, this module's own
    default_work_dir identity) assumes one file too -- silently treating a
    multi-file value as one path would resolve to a file that does not
    exist, and Terraform/kubectl would lose every context/cert merged in
    from the other files. Refuse with an actionable message instead of
    guessing which file in the list was meant.
    """
    raw = kubeconfig_path or os.environ.get("KUBECONFIG", str(Path.home() / ".kube" / "config"))
    if os.pathsep in raw:
        raise InstallError(
            f"--kubeconfig (or the ambient KUBECONFIG environment variable) is a {os.pathsep!r}-separated "
            f"list of files ({raw!r}): kubectl merges these into one logical view, but Terraform's "
            "kubernetes/helm providers -- and this tool's own KUBECONFIG_PATH handling -- only ever "
            "consume a single file. Flatten it first, e.g. "
            "`KUBECONFIG=<the same list> kubectl config view --flatten > merged-kubeconfig.yaml`, then "
            "pass --kubeconfig merged-kubeconfig.yaml."
        )
    return str(Path(raw).expanduser().resolve())


def resolve_storage_values_override(config_dir: Path, storage_values_override: Path | None) -> str:
    """Resolve SEAWEEDFS_OVERRIDE_VALUES_FILE for this run, persisting a
    --storage-values-override selection into `config_dir` (`<work-dir>/config`,
    never wiped -- see run_install) so it survives every later run against
    the same target -- including an upgrade or reconciliation that omits
    the flag.

    Without this, an install that started PVC-backed would silently revert
    to the bundle's emptyDir values the moment --storage-values-override is
    not repeated on some later run, which can fail the Helm upgrade on an
    immutable PVC field or, worse, succeed and quietly move data back onto
    ephemeral storage. Copying the CONTENT into `config_dir` (rather than
    remembering the original path) also survives the original file moving
    or being deleted, and keeps the override outside the bundle's own
    extraction tree the same way `<work-dir>/state/` keeps Terraform's own
    state outside it -- see `unpack_install_bundle`.

    Returns the empty string if no override was ever selected for this
    work directory.
    """
    persisted_path = config_dir / _PERSISTED_STORAGE_OVERRIDE_NAME
    if storage_values_override is not None:
        resolved_override = storage_values_override.expanduser().resolve()
        if not resolved_override.is_file():
            raise InstallError(f"--storage-values-override file not found: {resolved_override}")
        persisted_path.write_bytes(resolved_override.read_bytes())
        return str(persisted_path)
    if persisted_path.is_file():
        return str(persisted_path)
    return ""


def default_work_dir(kube_context: str, namespace: str, kubeconfig_path: str) -> Path:
    """Stable per-(kubeconfig, cluster, namespace) work directory, keyed by
    the resolved --kubeconfig path, --kube-context, and --namespace rather
    than freshly randomized on every invocation.

    A fresh temp directory every run would lose the Terraform state kept
    under `<work-dir>/state/` (see TERRAFORM_STATE_PATH in run_install): the
    next `olf install run` against the same target would find no matching
    state and either fail to reconcile an already-existing namespace or,
    without the create-or-refuse precondition, risk adopting a namespace it
    does not actually own. Keying by kubeconfig+context+namespace keeps a
    stable, safe default while still letting different install targets on
    the same machine use different directories automatically -- context
    names are only unique *within* a kubeconfig file, so two different
    kubeconfigs (e.g. two clusters both using the common name
    "kind-cluster") must not collide on context name alone.

    The identity hash covers the RAW --kube-context and --kubeconfig values,
    not the sanitized directory-name prefix: sanitizing non-alphanumeric
    characters to "_" is lossy (e.g. contexts "team/prod" and "team_prod"
    both sanitize to "team_prod"), so two distinct contexts differing only
    in punctuation must not collide on the sanitized prefix alone.
    """
    safe_context = re.sub(r"[^A-Za-z0-9._-]", "_", kube_context)
    safe_namespace = re.sub(r"[^A-Za-z0-9._-]", "_", namespace)
    identity = f"{kube_context}\n{Path(kubeconfig_path).expanduser()}"
    identity_key = hashlib.sha256(identity.encode()).hexdigest()[:16]
    return Path.home() / ".openlakeforge" / "install" / f"{safe_context}-{identity_key}" / safe_namespace


_TARGET_IDENTITY_FILENAME = "target-identity.json"


def resolve_cluster_uid(*, kube_context: str, kubeconfig_path: str, run: Any = subprocess.run) -> str:
    """A fingerprint of the cluster itself, independent of which kubeconfig
    path and context name happen to point at it right now.

    (kubeconfig_path, kube_context) is a pointer, not an identity: deleting
    and recreating a cluster under the same kubeconfig path and context name
    (e.g. `kind delete cluster --name X && kind create cluster --name X`)
    leaves that pointer textually unchanged while it now targets a wholly
    different cluster. `kube-system` is created fresh with a new random UID
    every time a cluster is created and always exists on any real
    Kubernetes cluster, so its namespace UID is a stable, cluster-scoped
    identifier check_work_dir_target_identity can compare -- unlike the
    pointer, it changes exactly when the underlying cluster does.
    """
    result = run(
        ["kubectl", "--context", kube_context, "get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "KUBECONFIG": kubeconfig_path},
    )
    cluster_uid = (result.stdout or "").strip()
    if result.returncode != 0 or not cluster_uid:
        raise InstallError(
            f"could not resolve a cluster identity via kube-context {kube_context!r}: "
            f"{(result.stderr or '').strip() or 'kube-system namespace UID was empty'}"
        )
    return cluster_uid


def check_work_dir_target_identity(
    config_dir: Path, *, kube_context: str, kubeconfig_path: str, namespace: str, run: Any = subprocess.run
) -> None:
    """Refuse to reuse this work directory for a different install target
    than the one it was first used for.

    default_work_dir derives its directory from (kubeconfig, kube_context,
    namespace), so two different targets never collide by construction --
    but an explicit --work-dir bypasses that derivation entirely, and the
    Terraform state kept under `<work-dir>/state/` (see
    TERRAFORM_STATE_PATH in run_install) is target-scoped by nature. Reusing
    the same directory for a different --namespace makes the running
    kubernetes_namespace_v1.lakehouse resource in state appear to already
    be that new namespace; since a Kubernetes namespace's name forces
    resource replacement, `terraform apply` can DELETE the first
    installation's namespace (and every lakehouse resource in it) to create
    the second one. Reusing it for a different --kube-context/--kubeconfig
    instead applies stale state against a cluster that never had those
    resources -- Terraform can adopt/manage an unrelated same-named
    resource there while losing all track of (orphaning) the first
    installation's actual running resources.

    Checked in two stages so a work directory pointed at an outright
    different target is rejected without ever touching the network. First,
    the cheap (kubeconfig, kube_context, namespace) POINTER is compared
    against what's recorded -- no cluster contact needed, and this alone
    settles the vast majority of mismatches. Only once the pointer matches
    -- meaning this run is about to operate against whatever cluster it
    currently resolves to -- is the cluster's own identity (see
    resolve_cluster_uid) resolved and compared too. That second stage
    catches what the pointer alone cannot: the cluster behind an unchanged
    kubeconfig/context/namespace being deleted and recreated under the same
    name (e.g. `kind delete cluster --name X && kind create cluster --name
    X`), which leaves the pointer textually identical while the persisted
    Terraform state now describes a cluster that no longer exists --
    applying it against the replacement risks Terraform altering or
    destroying unrelated (possibly foreign) resources there.

    Records (kubeconfig, kube_context, namespace, cluster_uid) in a marker
    file (`<work-dir>/config/target-identity.json`) on first use, and
    refuses on any later mismatch of either part rather than letting stale
    state drive a different target.
    """
    pointer = {"kubeconfig_path": kubeconfig_path, "kube_context": kube_context, "namespace": namespace}
    marker_path = config_dir / _TARGET_IDENTITY_FILENAME

    if marker_path.is_file():
        recorded = json.loads(marker_path.read_text())
        recorded_pointer = {key: recorded.get(key) for key in pointer}
        if recorded_pointer != pointer:
            raise InstallError(
                f"--work-dir {config_dir.parent} was already used for a different install target "
                f"(kubeconfig={recorded['kubeconfig_path']!r}, kube-context={recorded['kube_context']!r}, "
                f"namespace={recorded['namespace']!r}); this run targets "
                f"(kubeconfig={pointer['kubeconfig_path']!r}, kube-context={pointer['kube_context']!r}, "
                f"namespace={pointer['namespace']!r}). Reusing the same work directory for a different "
                "target can delete or orphan the first installation's resources, since the Terraform state "
                "inside it is target-scoped. Point --work-dir at a fresh directory, or at the one already "
                "used for this exact target."
            )

        cluster_uid = resolve_cluster_uid(kube_context=kube_context, kubeconfig_path=kubeconfig_path, run=run)
        if recorded.get("cluster_uid") != cluster_uid:
            raise InstallError(
                f"--work-dir {config_dir.parent} was already used for kubeconfig={kubeconfig_path!r}, "
                f"kube-context={kube_context!r}, namespace={namespace!r}, but against a DIFFERENT cluster "
                f"(recorded cluster identity {recorded.get('cluster_uid', '<unknown>')!r}; this kubeconfig/"
                f"context currently resolves to cluster identity {cluster_uid!r}). This usually means the "
                "cluster behind this kubeconfig/context was deleted and recreated under the same name -- e.g. "
                "`kind delete cluster` followed by `kind create cluster` with the same name. Reusing this "
                "work directory's Terraform state against the replacement cluster risks Terraform altering "
                "or destroying unrelated resources there. Point --work-dir at a fresh directory."
            )
        return

    cluster_uid = resolve_cluster_uid(kube_context=kube_context, kubeconfig_path=kubeconfig_path, run=run)
    marker_path.write_text(json.dumps({**pointer, "cluster_uid": cluster_uid}, indent=2, sort_keys=True) + "\n")


def check_namespace_ownership(
    *,
    terraform_dir: Path,
    terraform_state_path: Path,
    namespace: str,
    kube_context: str,
    kubeconfig_path: str,
    adopt_namespace: bool,
    run: Any = subprocess.run,
) -> None:
    """Refuse to let Terraform apply against a namespace this install does
    not already own, unless --adopt-namespace explicitly opts in.

    This is cross-environment behavioral policy (AGENTS.md #4: "Python for
    behaviour, shell for structure" -- ADR 0017), so it belongs here, tested,
    rather than as a bash decision inside platform-up.sh: a namespace
    `olf install` finds with no Terraform-state record of owning it may be
    entirely unrelated (unlike `make local-up`'s disposable, fully-owned kind
    cluster, where the same situation just means dev-loop drift, safe to
    auto-reset -- see reset_drifted_local_platform_if_needed). If Terraform
    already owns the namespace, this is a normal re-run/upgrade -- proceed.
    If the namespace doesn't exist yet, `terraform apply` creates it fresh --
    proceed. Otherwise, refuse with a non-zero exit unless the caller passed
    --adopt-namespace.

    Needs its own `terraform init` against the SAME backend path
    `platform-up.sh` uses (read-only `state show` after init requires no
    cluster connectivity, so this is safe and cheap to run before the phase
    script's own init) -- Terraform has no other way to answer "does state
    already own this resource" without one.
    """
    init_result = run(
        [
            "terraform",
            f"-chdir={terraform_dir}",
            "init",
            "-input=false",
            f"-backend-config=path={terraform_state_path}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if init_result.returncode != 0:
        raise InstallError(
            f"terraform init failed while checking namespace ownership: {(init_result.stderr or '').strip()}"
        )

    state_result = run(
        ["terraform", f"-chdir={terraform_dir}", "state", "show", "kubernetes_namespace_v1.lakehouse"],
        capture_output=True,
        text=True,
        check=False,
    )
    if state_result.returncode == 0:
        return  # already managed by this work directory -- normal re-run/upgrade

    namespace_result = run(
        ["kubectl", "--context", kube_context, "get", "namespace", namespace],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "KUBECONFIG": kubeconfig_path},
    )
    if namespace_result.returncode != 0:
        return  # doesn't exist yet -- terraform apply will create it

    if adopt_namespace:
        return

    raise InstallError(
        f"namespace {namespace!r} already exists, but this work directory has no record of managing "
        "it. olf install will not silently adopt a namespace it did not create. Re-run with "
        "--adopt-namespace if you intend olf install to take ownership of it, or point --namespace at "
        "an empty or nonexistent namespace."
    )


# --- Manifest-derived deploy inputs ----------------------------------------


def repository_and_release_tag(resolved_image_ref: str, version: str) -> tuple[str, str]:
    """Split a manifest resolved_images value (`repo@sha256:...`) into (repo, version).

    See module docstring: the release pipeline guarantees `repo:version`
    resolves to exactly this digest, so the existing repository/tag Terraform
    variables (unchanged from `make local-up`) are all that is needed.
    """
    repository, separator, digest = resolved_image_ref.partition("@")
    if not repository or not separator or not digest:
        raise InstallError(f"malformed resolved image reference: {resolved_image_ref!r}")
    return repository, version


def profile_env(profile: str) -> dict[str, str]:
    try:
        return dict(_PROFILE_ENV[profile])
    except KeyError as exc:
        raise InstallError(f"unknown --profile {profile!r}; expected 'slim' or 'full'") from exc


# --- Install orchestration --------------------------------------------------


@dataclass(frozen=True)
class InstallOptions:
    tag: str
    kube_context: str
    repo_slug: str = DEFAULT_REPO_SLUG
    namespace: str = "lakehouse"
    profile: str = "full"
    cluster_name: str = "openlakeforge-install"
    kubeconfig_path: str | None = None
    assets_dir: Path | None = None
    work_dir: Path | None = None
    skip_artifacts: bool = False
    oidc_issuer: str = DEFAULT_OIDC_ISSUER
    # DANGEROUS: only for local/CI rehearsal against an unsigned dry-run
    # bundle (make release-bundle never signs anything). Never set this
    # against a real release -- it defeats the supply-chain guarantee.
    skip_signature_verification: bool = False
    # Only for rehearsal against images built locally and never pushed to a
    # registry (e.g. `make release-bundle`): such an image has no
    # registry-assigned manifest digest at all (confirmed empirically --
    # `docker image inspect --format='{{.Id}}'` returns the *config* digest,
    # a different value from what containerd/kubelet report once the image
    # is `kind load docker-image`-ed, and there is no resolvable
    # `repo@digest` reference for it in the first place). Falls Phase 2 back
    # to patching by tag, same as before digest re-pinning was added.
    skip_digest_pin: bool = False
    # Layered on top of the bundle's infra/helm/values/local/seaweedfs.yaml
    # (e.g. to switch data volumes off emptyDir onto a PersistentVolumeClaim).
    # Must live outside the extracted bundle tree: unpack_install_bundle
    # deletes and re-extracts that tree on every install/upgrade, so an
    # override edited inside it would silently revert on the next run.
    storage_values_override: Path | None = None
    # olf install never silently adopts a namespace it did not create --
    # see platform-up.sh's refuse_unmanaged_existing_namespace_unless_adopting.
    # Set this to explicitly take ownership of a pre-existing namespace this
    # work directory's Terraform state has no record of managing.
    adopt_namespace: bool = False


def run_phase_script(script: Path, *, env: Mapping[str, str], run: Any = subprocess.run) -> None:
    if not script.is_file():
        raise InstallError(f"phase script not found in the install bundle: {script}")
    result = run(["bash", str(script)], env=dict(env), check=False)
    if result.returncode != 0:
        raise InstallError(f"{script.name} failed with exit code {result.returncode}")


def run_install(
    options: InstallOptions,
    *,
    run: Any = subprocess.run,
    http_session: Any = None,
) -> dict[str, Any]:
    """Fetch (or reuse), verify, unpack, and apply a tagged release."""
    # Resolved to an absolute path up front: a relative --kubeconfig is
    # meaningful relative to the caller's cwd (where kubectl resolves it
    # too), but platform-up.sh's Terraform invocation runs with
    # -chdir=<bundle_root>/infra/terraform/environments/local, and that
    # root's own abspath(pathexpand(var.kubeconfig_path)) would resolve a
    # relative value against ITS cwd instead -- a different file, or none.
    resolved_kubeconfig_path = resolve_kubeconfig_path(options.kubeconfig_path)
    # Resolved to absolute for the same reason: TERRAFORM_STATE_PATH below
    # becomes a Terraform -backend-config="path=..." value, which Terraform
    # resolves relative to the *root module's* directory (the bundle's
    # -chdir target) when given a relative path -- not this process's own
    # cwd, where a relative --work-dir would otherwise be meaningful.
    raw_work_dir = options.work_dir or default_work_dir(
        options.kube_context, options.namespace, resolved_kubeconfig_path
    )
    work_dir = raw_work_dir.expanduser().resolve()
    # bundle/ is wiped and re-extracted on every run (unpack_install_bundle)
    # with zero exceptions; state/ and config/ never are -- Terraform's own
    # state (an explicit backend path, not a file inside the Terraform root)
    # and this tool's own durable records (storage override, target
    # identity) live here specifically so a bundle wipe can never touch
    # them.
    state_dir = work_dir / "state"
    config_dir = work_dir / "config"
    work_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    check_work_dir_target_identity(
        config_dir,
        kube_context=options.kube_context,
        kubeconfig_path=resolved_kubeconfig_path,
        namespace=options.namespace,
        run=run,
    )
    # Resolved and persisted before unpack_install_bundle runs, not after:
    # if a caller pointed --storage-values-override at a path inside
    # --work-dir/bundle/ (the one place the docs say never to put it),
    # reading it AFTER extraction would silently read back whatever the
    # release's own re-extraction just put there instead of what the
    # caller actually intended -- appearing to succeed while persisting the
    # wrong (possibly ephemeral-storage) values.
    storage_values_override_path = resolve_storage_values_override(config_dir, options.storage_values_override)

    if options.assets_dir is not None:
        # Resolved up front, same reasoning as --kubeconfig/--work-dir
        # above: a relative --assets-dir is meaningful against this
        # process's cwd, but the resulting manifest_path is handed back to
        # the caller (cli.py prints it in the 'olf install verify' follow-up
        # command) to be reused later, possibly from a different directory,
        # where the same relative value would resolve to a different file
        # or none.
        assets = ReleaseAssets(options.assets_dir.expanduser().resolve())
    else:
        assets = fetch_release_assets(
            options.tag, repo_slug=options.repo_slug, dest_dir=work_dir / "assets", session=http_session
        )

    identity_regexp = certificate_identity_regexp(options.repo_slug, options.tag)
    checksums_path = assets.path("checksums.txt")
    if not checksums_path.is_file():
        raise InstallError(f"checksums.txt not found in {assets.directory}")

    if options.skip_signature_verification:
        log.warn(
            "Skipping cosign signature verification (--skip-signature-verification). "
            "Never use this against a real release."
        )
    else:
        bundle_signature_path = assets.path("checksums.txt.bundle")
        if not bundle_signature_path.is_file():
            raise InstallError(f"checksums.txt.bundle not found in {assets.directory}")
        log.step(f"Authenticating checksums.txt with its keyless Sigstore bundle for {options.tag}...")
        cosign_verify_blob(
            checksums_path,
            bundle_signature_path,
            identity_regexp=identity_regexp,
            oidc_issuer=options.oidc_issuer,
            run=run,
        )

    log.step("Verifying release asset checksums...")
    verify_checksums(assets.directory, checksums_path.read_text())

    manifest_path = assets.path("component-manifest.json")
    if not manifest_path.is_file():
        raise InstallError(f"component-manifest.json not found in {assets.directory}")
    manifest = json.loads(manifest_path.read_text())

    if not options.skip_signature_verification:
        for image_name in ("project-code", "superset"):
            reference = manifest["resolved_images"][image_name]
            log.step(f"Verifying cosign signature for {reference}...")
            cosign_verify_image(reference, identity_regexp=identity_regexp, oidc_issuer=options.oidc_issuer, run=run)

    bundle_path = assets.path("install-bundle.tar.gz")
    if not bundle_path.is_file():
        raise InstallError(f"install-bundle.tar.gz not found in {assets.directory}")
    log.step("Unpacking install bundle...")
    bundle_root = unpack_install_bundle(bundle_path, work_dir / "bundle")

    # Behavioral policy (AGENTS.md #4), not structure -- checked here in
    # Python, before platform-up.sh ever runs, rather than as a bash
    # decision inside it. See check_namespace_ownership's own docstring.
    check_namespace_ownership(
        terraform_dir=bundle_root / "infra" / "terraform" / "environments" / "local",
        terraform_state_path=state_dir / "terraform.tfstate",
        namespace=options.namespace,
        kube_context=options.kube_context,
        kubeconfig_path=resolved_kubeconfig_path,
        adopt_namespace=options.adopt_namespace,
        run=run,
    )

    version = manifest["distribution"]["version"]
    project_code_repository, project_code_tag = repository_and_release_tag(
        manifest["resolved_images"]["project-code"], version
    )
    superset_repository, superset_tag = repository_and_release_tag(manifest["resolved_images"]["superset"], version)
    # The exact cosign-verified digests, passed through so Phase 2 can patch
    # every running container to the authenticated bytes directly -- see the
    # module docstring: repo:version is guaranteed to resolve to this same
    # digest today, but a tag can be moved after publication (registry
    # compromise, misconfigured push access) without moving the digest. A
    # kubectl-patched image reference is not limited by whether the Helm
    # chart itself supports digest pinning (Superset's does not).
    project_code_digest = ""
    superset_digest = ""
    if not options.skip_digest_pin:
        project_code_digest = manifest["resolved_images"]["project-code"].rpartition("@")[2]
        superset_digest = manifest["resolved_images"]["superset"].rpartition("@")[2]

    # Manifest-resolved and digest-pinned (release/component-catalog.yaml),
    # so Phase 2's Floe container runs the exact bytes the catalog records
    # rather than floe-manifest.sh's own hardcoded default drifting out of
    # sync with a specific release -- that container gets a writable bind
    # mount of the entire extracted bundle, so a mutable tag would let a
    # moved image inject arbitrary code into Phase 2 unverified.
    floe_image = manifest["catalog"]["components"]["images"].get("floe", "")

    env = os.environ.copy()
    env.update(
        {
            "NAMESPACE": options.namespace,
            "CLUSTER_NAME": options.cluster_name,
            "KUBE_CONTEXT": options.kube_context,
            "KUBECONFIG_PATH": resolved_kubeconfig_path,
            # configure_deployment_scope (scripts/lib/common.sh) re-derives
            # KUBECONFIG from KUBECONFIG_PATH before either phase script's
            # first kubectl call, but set it here too rather than rely on
            # every current and future kubectl invocation in the bundle
            # running after that point -- a stale inherited KUBECONFIG (from
            # this process's own os.environ, copied above) must never win.
            "KUBECONFIG": resolved_kubeconfig_path,
            "DEPLOYMENT_SCOPE": "local",
            "FOUNDATION_MODE": "existing-cluster",
            # Terraform's actual resource state, as a `-backend-config`
            # path -- see infra/terraform/environments/local/main.tf's
            # `backend "local" {}` block -- kept under <work-dir>/state/,
            # never inside <work-dir>/bundle/ where unpack_install_bundle
            # would wipe it on every run.
            "TERRAFORM_STATE_PATH": str(state_dir / "terraform.tfstate"),
            "SEAWEEDFS_OVERRIDE_VALUES_FILE": storage_values_override_path,
            "FLOE_IMAGE": floe_image,
            "PROJECT_CODE_IMAGE_REPOSITORY": project_code_repository,
            "PROJECT_CODE_IMAGE_TAG": project_code_tag,
            "PROJECT_CODE_IMAGE_DIGEST": project_code_digest,
            "PROJECT_CODE_IMAGE_PULL_POLICY": "IfNotPresent",
            "PROJECT_CODE_IMAGE_REVISION": manifest["distribution"]["git_sha"],
            "SUPERSET_IMAGE_REPOSITORY": superset_repository,
            "SUPERSET_IMAGE_TAG": superset_tag,
            "SUPERSET_IMAGE_DIGEST": superset_digest,
            "SUPERSET_IMAGE_PULL_POLICY": "IfNotPresent",
        }
    )
    env.update(profile_env(options.profile))

    log.step(f"Applying the OpenLakeForge platform ({options.profile} profile) to {options.kube_context}...")
    run_phase_script(bundle_root / "scripts/local/stack/platform-up.sh", env=env, run=run)

    if not options.skip_artifacts:
        log.step("Deploying dynamic artifacts...")
        run_phase_script(bundle_root / "scripts/local/stack/deploy-artifacts.sh", env=env, run=run)
    else:
        log.warn("Skipping artifact deployment (--skip-artifacts): only the static platform was applied.")

    return {
        "manifest": manifest,
        "manifest_path": manifest_path,
        "bundle_root": bundle_root,
        "work_dir": work_dir,
        "resolved_kubeconfig_path": resolved_kubeconfig_path,
    }


# --- Verify: prove running digests match the manifest -----------------------


@dataclass(frozen=True)
class ImageMismatch:
    repository: str
    expected: str
    observed: tuple[str, ...]


@dataclass(frozen=True)
class ParityReport:
    matched: tuple[str, ...]
    missing: tuple[str, ...]
    mismatched: tuple[ImageMismatch, ...]
    unrecognized: tuple[str, ...]

    def ok(self, *, strict: bool = False) -> bool:
        if self.missing or self.mismatched:
            return False
        return not (strict and self.unrecognized)

    def render(self) -> str:
        lines = [f"Matched ({len(self.matched)}): {', '.join(self.matched) or '(none)'}"]
        if self.missing:
            lines.append(f"MISSING (expected for this profile, not observed running): {', '.join(self.missing)}")
        for mismatch in self.mismatched:
            lines.append(
                f"MISMATCH {mismatch.repository}: manifest={mismatch.expected} running={', '.join(mismatch.observed)}"
            )
        if self.unrecognized:
            lines.append(f"Running images not declared in the manifest: {', '.join(self.unrecognized)}")
        return "\n".join(lines)


_DEFAULT_REGISTRY = "docker.io"
_DEFAULT_NAMESPACE = "library"


def canonical_repository(reference: str) -> str:
    """Canonicalize an image reference's repository the way containerd/kubelet
    report it in a container's resolved `imageID`: strips any `@digest` and
    `:tag`, expands an implicit Docker Hub registry to `docker.io/...`, and
    an implicit `library/` namespace for single-segment Docker Hub names
    (`postgres` -> `docker.io/library/postgres`). Explicit registries
    (`ghcr.io/...`, `docker.getcollate.io/...`) and already-qualified
    references pass through unchanged apart from stripping the tag/digest.

    `release/component-catalog.yaml` entries are `name:tag@sha256:...`
    (Docker's usual shorthand); real running images resolve to this fully
    qualified form. Without this normalization the two never compare equal.
    """
    repository = reference.partition("@")[0]
    # A ':' in the final path segment is a tag; a ':' earlier (host:port) is not.
    head, sep, last_segment = repository.rpartition("/")
    if ":" in last_segment:
        last_segment = last_segment.split(":", 1)[0]
        repository = f"{head}{sep}{last_segment}"

    first_segment = repository.split("/", 1)[0]
    has_explicit_registry = "." in first_segment or ":" in first_segment or first_segment == "localhost"
    if not has_explicit_registry:
        repository = f"{_DEFAULT_REGISTRY}/{repository}"

    parts = repository.split("/")
    if parts[0] == _DEFAULT_REGISTRY and len(parts) == 2:
        repository = f"{_DEFAULT_REGISTRY}/{_DEFAULT_NAMESPACE}/{parts[1]}"

    return repository


def expected_images(
    manifest: Mapping[str, Any],
    *,
    governance: bool,
    analytics: bool,
    skip_repositories: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Canonical repository -> expected `repo@sha256:...` reference for the active profile.

    `skip_repositories` (canonical repository strings) exists for rehearsing
    against images built locally and never pushed to a registry: such an
    image has no registry-assigned digest to prove parity against at all
    (see InstallOptions.skip_digest_pin) -- there is no correct expected
    value for it, so it is excluded rather than reported as permanently
    missing or mismatched. Never used for a real install.
    """
    catalog_images = manifest["catalog"]["components"]["images"]
    resolved_images = manifest["resolved_images"]

    catalog_names = list(_PROFILE_CATALOG_IMAGES["always"])
    resolved_names = list(_PROFILE_RESOLVED_IMAGES["always"])
    if governance:
        catalog_names += _PROFILE_CATALOG_IMAGES["governance"]
    if analytics:
        catalog_names += _PROFILE_CATALOG_IMAGES["analytics"]
        resolved_names += _PROFILE_RESOLVED_IMAGES["analytics"]

    expected: dict[str, str] = {}
    for name in catalog_names:
        reference = catalog_images[name]
        expected[canonical_repository(reference)] = reference
    for name in resolved_names:
        reference = resolved_images[name]
        expected[canonical_repository(reference)] = reference
    for repository in skip_repositories:
        expected.pop(repository, None)
    return expected


def check_parity(
    expected: Mapping[str, str],
    observed_entries: Sequence[Mapping[str, str]],
    *,
    skip_repositories: frozenset[str] = frozenset(),
) -> ParityReport:
    """Compare expected repo->digest against observed pod container imageIDs.

    `skip_repositories` (see `expected_images`) is applied on the observed
    side too, not just by omitting the repository from `expected`: an image
    that resolves normally to a `repo@digest` imageID would otherwise still
    be collected and surface as "unrecognized" once its repository is no
    longer in `expected` -- exactly the noise `--skip-image` exists to
    suppress.
    """
    observed_by_repository: dict[str, set[str]] = {}
    for entry in observed_entries:
        image_id = entry.get("image_id") or ""
        if "@" not in image_id:
            continue  # not yet resolved (pod pending/not ready)
        repository = canonical_repository(image_id)
        if repository in skip_repositories:
            continue
        digest = image_id.rpartition("@")[2]
        observed_by_repository.setdefault(repository, set()).add(digest)

    matched: list[str] = []
    missing: list[str] = []
    mismatched: list[ImageMismatch] = []
    for repository, expected_reference in sorted(expected.items()):
        expected_digest = expected_reference.rpartition("@")[2]
        observed_digests = observed_by_repository.pop(repository, None)
        if not observed_digests:
            missing.append(repository)
        elif observed_digests == {expected_digest}:
            matched.append(repository)
        else:
            observed_references = tuple(sorted(f"{repository}@{digest}" for digest in observed_digests))
            mismatched.append(ImageMismatch(repository, expected_reference, observed_references))

    unrecognized = sorted(observed_by_repository)
    return ParityReport(tuple(matched), tuple(missing), tuple(mismatched), tuple(unrecognized))


def run_verify(
    manifest: Mapping[str, Any],
    *,
    namespace: str,
    kube_context: str,
    governance: bool,
    analytics: bool,
    kubeconfig_path: str | None = None,
    skip_repositories: frozenset[str] = frozenset(),
) -> ParityReport:
    from olf import k8s

    expected = expected_images(
        manifest, governance=governance, analytics=analytics, skip_repositories=skip_repositories
    )
    observed = k8s.list_pod_images(namespace, kube_context=kube_context, kubeconfig_path=kubeconfig_path)
    return check_parity(expected, observed, skip_repositories=skip_repositories)
