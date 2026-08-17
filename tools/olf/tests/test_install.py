from __future__ import annotations

import hashlib
import json
import os
import tarfile
from pathlib import Path

import pytest
import yaml

from olf import install

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parents[3]


def _manifest() -> dict:
    return json.loads((FIXTURES / "component-manifest.json").read_text())


# --- checksums ---------------------------------------------------------


def test_parse_checksums_round_trips_sha256sum_format() -> None:
    text = "aaaa  a.txt\nbbbb  nested/b.txt\n"
    assert install.parse_checksums(text) == [("aaaa", "a.txt"), ("bbbb", "nested/b.txt")]


def test_parse_checksums_rejects_malformed_line() -> None:
    with pytest.raises(install.InstallError):
        install.parse_checksums("not-a-checksum-line\n")


def test_verify_checksums_passes_for_matching_digests(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    digest = hashlib.sha256(b"hello").hexdigest()
    install.verify_checksums(tmp_path, f"{digest}  a.txt\n")


def test_verify_checksums_rejects_mismatch(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    with pytest.raises(install.InstallError, match="checksum mismatch"):
        install.verify_checksums(tmp_path, ("0" * 64) + "  a.txt\n")


def test_verify_checksums_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(install.InstallError, match="missing"):
        install.verify_checksums(tmp_path, ("0" * 64) + "  missing.txt\n")


# --- cosign identity ------------------------------------------------------


def test_certificate_identity_regexp_matches_exact_tag() -> None:
    import re

    pattern = install.certificate_identity_regexp("malon64/openlakeforge", "v0.1.0-alpha.1")
    assert re.match(
        pattern, "https://github.com/malon64/openlakeforge/.github/workflows/release.yml@refs/tags/v0.1.0-alpha.1"
    )
    # The escaped '.' must not behave as a regex wildcard.
    assert not re.match(
        pattern, "https://github.com/malon64/openlakeforgeX/.github/workflows/release.yml@refs/tags/v0x1x0-alphaX1"
    )


def test_cosign_verify_blob_raises_on_nonzero_exit() -> None:
    class FakeResult:
        returncode = 1
        stderr = "signature invalid"

    with pytest.raises(install.InstallError, match="signature invalid"):
        install.cosign_verify_blob(
            Path("checksums.txt"),
            Path("checksums.txt.bundle"),
            identity_regexp="^x$",
            run=lambda *a, **k: FakeResult(),
        )


def test_cosign_verify_image_passes_on_zero_exit() -> None:
    class FakeResult:
        returncode = 0
        stderr = ""

    reference = "ghcr.io/x/y@sha256:" + "a" * 64
    install.cosign_verify_image(reference, identity_regexp="^x$", run=lambda *a, **k: FakeResult())


# --- bundle unpack ---------------------------------------------------------


def _make_bundle(tmp_path: Path, name: str, bundle_filename: str, files: dict[str, str]) -> Path:
    bundle_dir = tmp_path / name
    bundle_dir.mkdir(exist_ok=True)
    for relative_path, content in files.items():
        path = bundle_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    bundle_path = tmp_path / bundle_filename
    with tarfile.open(bundle_path, "w:gz") as archive:
        archive.add(bundle_dir, arcname=name)
    return bundle_path


def test_unpack_install_bundle_strips_the_versioned_prefix(tmp_path: Path) -> None:
    bundle_path = _make_bundle(tmp_path, "openlakeforge-0.1.0-alpha.1", "install-bundle.tar.gz", {"marker.txt": "hi"})

    dest = tmp_path / "extracted"
    result = install.unpack_install_bundle(bundle_path, dest)

    assert result == dest
    assert (dest / "marker.txt").read_text() == "hi"
    assert not (dest / "openlakeforge-0.1.0-alpha.1").exists()


def test_unpack_install_bundle_rejects_multiple_top_level_dirs(tmp_path: Path) -> None:
    bundle_path = tmp_path / "bad-bundle.tar.gz"
    with tarfile.open(bundle_path, "w:gz") as archive:
        for name in ("a", "b"):
            member_dir = tmp_path / name
            member_dir.mkdir()
            archive.add(member_dir, arcname=name)

    with pytest.raises(install.InstallError, match="exactly one top-level directory"):
        install.unpack_install_bundle(bundle_path, tmp_path / "extracted")


def test_unpack_install_bundle_is_idempotent_and_preserves_the_terraform_provider_cache(tmp_path: Path) -> None:
    bundle_path = _make_bundle(tmp_path, "openlakeforge-0.1.0-alpha.1", "install-bundle.tar.gz", {"marker.txt": "v1"})

    dest = tmp_path / "extracted"
    install.unpack_install_bundle(bundle_path, dest)
    # Simulate `terraform init` writing its provider plugin cache into the
    # extracted tree -- never part of the archive, so a repeat extraction
    # must not disturb it (avoids re-downloading providers every run).
    terraform_cache_dir = dest / ".terraform" / "providers"
    terraform_cache_dir.mkdir(parents=True)
    (terraform_cache_dir / "plugin-cache-marker").write_text("cached provider")

    # Re-extracting the SAME release in place must succeed and leave the
    # provider cache alone.
    result = install.unpack_install_bundle(bundle_path, dest)

    assert result == dest
    assert (terraform_cache_dir / "plugin-cache-marker").read_text() == "cached provider"


def test_unpack_install_bundle_wipes_everything_outside_the_terraform_provider_cache(tmp_path: Path) -> None:
    """Regression test: Terraform's actual STATE never lives inside the
    bundle tree at all (run_install keeps it under a separate <work-dir>/state/
    via an explicit backend path), so unlike the provider cache, nothing
    else gets a carve-out here -- any other untracked file, even one that
    looks state-like, must be wiped on re-extraction.
    """
    bundle_path = _make_bundle(tmp_path, "openlakeforge-0.1.0-alpha.1", "install-bundle.tar.gz", {"marker.txt": "v1"})

    dest = tmp_path / "extracted"
    install.unpack_install_bundle(bundle_path, dest)
    stray_file = dest / "terraform.tfstate"
    stray_file.write_text("this must not survive")

    install.unpack_install_bundle(bundle_path, dest)

    assert not stray_file.exists()


def test_unpack_install_bundle_upgrades_content_in_place_preserving_the_provider_cache(tmp_path: Path) -> None:
    """Installing a DIFFERENT tag into the same --work-dir must overwrite
    tracked content at the same stable path, so the Terraform provider
    plugin cache (kept here, unlike state -- see
    _PRESERVED_ACROSS_UPGRADES_DIR) doesn't need re-downloading on every
    upgrade.
    """
    old_bundle = _make_bundle(
        tmp_path,
        "openlakeforge-0.1.0-alpha.1",
        "old.tar.gz",
        {"marker.txt": "v1", "infra/terraform/environments/local/removed_module.tf": "old resource"},
    )
    dest = tmp_path / "extracted"
    install.unpack_install_bundle(old_bundle, dest)
    terraform_cache_dir = dest / "infra/terraform/environments/local/.terraform/providers"
    terraform_cache_dir.mkdir(parents=True)
    (terraform_cache_dir / "plugin-cache-marker").write_text("cached provider")

    new_bundle = _make_bundle(tmp_path, "openlakeforge-0.2.0-alpha.1", "new.tar.gz", {"marker.txt": "v2"})
    result = install.unpack_install_bundle(new_bundle, dest)

    assert result == dest
    assert (dest / "marker.txt").read_text() == "v2"
    assert (terraform_cache_dir / "plugin-cache-marker").read_text() == "cached provider"
    # A .tf file the new release removed must not linger -- Terraform loads
    # every *.tf file in the directory, so a stale one would keep applying.
    assert not (dest / "infra/terraform/environments/local/removed_module.tf").exists()


# --- default_work_dir -------------------------------------------------------


def test_default_work_dir_is_stable_for_the_same_target() -> None:
    first = install.default_work_dir("kind-openlakeforge-local", "lakehouse", "/home/user/.kube/config")
    second = install.default_work_dir("kind-openlakeforge-local", "lakehouse", "/home/user/.kube/config")
    assert first == second
    assert str(first).endswith("lakehouse")
    assert "kind-openlakeforge-local" in first.parent.name


def test_default_work_dir_differs_per_target() -> None:
    a = install.default_work_dir("kind-a", "lakehouse", "/home/user/.kube/config")
    b = install.default_work_dir("kind-b", "lakehouse", "/home/user/.kube/config")
    c = install.default_work_dir("kind-a", "lakehouse-slim", "/home/user/.kube/config")
    assert a != b
    assert a != c


def test_default_work_dir_disambiguates_same_context_name_in_different_kubeconfigs() -> None:
    # Two different clusters whose kubeconfigs both happen to name their
    # context "kind-cluster" must not resolve to the same Terraform state.
    a = install.default_work_dir("kind-cluster", "lakehouse", "/first/kubeconfig")
    b = install.default_work_dir("kind-cluster", "lakehouse", "/second/kubeconfig")
    assert a != b


# --- check_work_dir_target_identity -----------------------------------------


def _fake_cluster_uid_run(*, cluster_uid="fake-cluster-uid-1"):
    """Fake `run` for check_work_dir_target_identity: only ever asked for
    kube-system's namespace UID (see resolve_cluster_uid).
    """

    class FakeResult:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str):
            self.stdout = stdout

    def fake_run(args, **kwargs):
        assert args[0] == "kubectl" and "kube-system" in args
        return FakeResult(cluster_uid)

    return fake_run


def test_check_work_dir_target_identity_records_on_first_use(tmp_path: Path) -> None:
    install.check_work_dir_target_identity(
        tmp_path,
        kube_context="my-cluster",
        kubeconfig_path="/home/user/.kube/config",
        namespace="lakehouse",
        run=_fake_cluster_uid_run(cluster_uid="cluster-uid-1"),
    )

    marker = tmp_path / "target-identity.json"
    assert marker.is_file()
    assert json.loads(marker.read_text()) == {
        "kube_context": "my-cluster",
        "kubeconfig_path": "/home/user/.kube/config",
        "namespace": "lakehouse",
        "cluster_uid": "cluster-uid-1",
    }


def test_check_work_dir_target_identity_allows_the_same_target_again(tmp_path: Path) -> None:
    kwargs = {"kube_context": "my-cluster", "kubeconfig_path": "/home/user/.kube/config", "namespace": "lakehouse"}
    run = _fake_cluster_uid_run(cluster_uid="cluster-uid-1")
    install.check_work_dir_target_identity(tmp_path, run=run, **kwargs)

    install.check_work_dir_target_identity(tmp_path, run=run, **kwargs)  # must not raise


def test_check_work_dir_target_identity_rejects_a_different_namespace(tmp_path: Path) -> None:
    """This is the destructive case: reusing --work-dir for a different
    --namespace makes Terraform's preserved state believe the
    kubernetes_namespace_v1.lakehouse resource IS the new namespace, and
    since a namespace's name forces replacement, apply can delete the
    first installation's namespace (and everything in it) to create the
    second.
    """
    install.check_work_dir_target_identity(
        tmp_path,
        kube_context="my-cluster",
        kubeconfig_path="/home/user/.kube/config",
        namespace="lakehouse",
        run=_fake_cluster_uid_run(),
    )

    def fail_if_called(*args, **kwargs):
        raise AssertionError("a pointer mismatch must be rejected without ever resolving cluster identity")

    with pytest.raises(install.InstallError, match="different install target"):
        install.check_work_dir_target_identity(
            tmp_path,
            kube_context="my-cluster",
            kubeconfig_path="/home/user/.kube/config",
            namespace="analytics",
            run=fail_if_called,
        )


def test_check_work_dir_target_identity_rejects_a_different_kube_context(tmp_path: Path) -> None:
    install.check_work_dir_target_identity(
        tmp_path,
        kube_context="cluster-a",
        kubeconfig_path="/home/user/.kube/config",
        namespace="lakehouse",
        run=_fake_cluster_uid_run(),
    )

    with pytest.raises(install.InstallError, match="different install target"):
        install.check_work_dir_target_identity(
            tmp_path,
            kube_context="cluster-b",
            kubeconfig_path="/home/user/.kube/config",
            namespace="lakehouse",
            run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not resolve cluster identity")),
        )


def test_check_work_dir_target_identity_rejects_a_different_kubeconfig(tmp_path: Path) -> None:
    install.check_work_dir_target_identity(
        tmp_path,
        kube_context="my-cluster",
        kubeconfig_path="/first/kubeconfig",
        namespace="lakehouse",
        run=_fake_cluster_uid_run(),
    )

    with pytest.raises(install.InstallError, match="different install target"):
        install.check_work_dir_target_identity(
            tmp_path,
            kube_context="my-cluster",
            kubeconfig_path="/second/kubeconfig",
            namespace="lakehouse",
            run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not resolve cluster identity")),
        )


def test_check_work_dir_target_identity_rejects_a_recreated_cluster_with_the_same_pointer(tmp_path: Path) -> None:
    """The gap this whole cluster_uid mechanism exists to close: the same
    kubeconfig path, kube-context name, and namespace can persist across a
    `kind delete cluster && kind create cluster` -- but the cluster behind
    them is now a totally different one. kube-system's namespace UID
    changes on every cluster creation, so it must catch what the pointer
    alone cannot.
    """
    kwargs = {"kube_context": "my-cluster", "kubeconfig_path": "/home/user/.kube/config", "namespace": "lakehouse"}
    install.check_work_dir_target_identity(tmp_path, run=_fake_cluster_uid_run(cluster_uid="original-uid"), **kwargs)

    with pytest.raises(install.InstallError, match="DIFFERENT cluster"):
        install.check_work_dir_target_identity(
            tmp_path, run=_fake_cluster_uid_run(cluster_uid="recreated-uid"), **kwargs
        )


def test_resolve_cluster_uid_returns_the_kube_system_namespace_uid() -> None:
    def fake_run(args, **kwargs):
        assert args == [
            "kubectl",
            "--context",
            "my-cluster",
            "get",
            "namespace",
            "kube-system",
            "-o",
            "jsonpath={.metadata.uid}",
        ]
        assert kwargs["env"]["KUBECONFIG"] == "/home/user/.kube/config"

        class FakeResult:
            returncode = 0
            stdout = "abc-123-uid"
            stderr = ""

        return FakeResult()

    assert (
        install.resolve_cluster_uid(kube_context="my-cluster", kubeconfig_path="/home/user/.kube/config", run=fake_run)
        == "abc-123-uid"
    )


def test_resolve_cluster_uid_raises_when_kubectl_fails() -> None:
    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "connection refused"

    with pytest.raises(install.InstallError, match="connection refused"):
        install.resolve_cluster_uid(
            kube_context="my-cluster", kubeconfig_path="/home/user/.kube/config", run=lambda *a, **k: FakeResult()
        )


def _make_ownership_check_run(*, init_ok=True, state_owns=False, namespace_exists=False):
    """Build a fake `run` callable for check_namespace_ownership that routes
    on argv content, matching the three subprocess calls the function makes
    in sequence (terraform init, terraform state show, kubectl get
    namespace).
    """
    calls: list[list[str]] = []

    class FakeResult:
        def __init__(self, returncode: int):
            self.returncode = returncode
            self.stdout = ""
            self.stderr = "" if returncode == 0 else "boom"

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[0] == "terraform" and "init" in args:
            return FakeResult(0 if init_ok else 1)
        if args[0] == "terraform" and "state" in args:
            return FakeResult(0 if state_owns else 1)
        if args[0] == "kubectl":
            return FakeResult(0 if namespace_exists else 1)
        raise AssertionError(f"unexpected command: {args}")

    return fake_run, calls


def test_check_namespace_ownership_proceeds_when_state_already_owns_it(tmp_path: Path) -> None:
    fake_run, calls = _make_ownership_check_run(state_owns=True)

    install.check_namespace_ownership(
        terraform_dir=tmp_path / "terraform",
        terraform_state_path=tmp_path / "state" / "terraform.tfstate",
        namespace="lakehouse",
        kube_context="my-cluster",
        kubeconfig_path="/home/user/.kube/config",
        adopt_namespace=False,
        run=fake_run,
    )

    # Namespace existence is never even checked -- state ownership alone settles it.
    assert not any(c[0] == "kubectl" for c in calls)


def test_check_namespace_ownership_proceeds_when_namespace_does_not_exist_yet() -> None:
    fake_run, _ = _make_ownership_check_run(state_owns=False, namespace_exists=False)

    install.check_namespace_ownership(
        terraform_dir=Path("/tf"),
        terraform_state_path=Path("/state/terraform.tfstate"),
        namespace="lakehouse",
        kube_context="my-cluster",
        kubeconfig_path="/home/user/.kube/config",
        adopt_namespace=False,
        run=fake_run,
    )  # must not raise


def test_check_namespace_ownership_proceeds_when_adopting() -> None:
    fake_run, _ = _make_ownership_check_run(state_owns=False, namespace_exists=True)

    install.check_namespace_ownership(
        terraform_dir=Path("/tf"),
        terraform_state_path=Path("/state/terraform.tfstate"),
        namespace="lakehouse",
        kube_context="my-cluster",
        kubeconfig_path="/home/user/.kube/config",
        adopt_namespace=True,
        run=fake_run,
    )  # must not raise


def test_check_namespace_ownership_refuses_an_unowned_existing_namespace() -> None:
    """The destructive case this whole function exists to prevent: a
    namespace that already exists, that this work directory's Terraform
    state has no record of managing, and that the caller did not explicitly
    opt in to adopting.
    """
    fake_run, _ = _make_ownership_check_run(state_owns=False, namespace_exists=True)

    with pytest.raises(install.InstallError, match="already exists"):
        install.check_namespace_ownership(
            terraform_dir=Path("/tf"),
            terraform_state_path=Path("/state/terraform.tfstate"),
            namespace="lakehouse",
            kube_context="my-cluster",
            kubeconfig_path="/home/user/.kube/config",
            adopt_namespace=False,
            run=fake_run,
        )


def test_check_namespace_ownership_raises_when_terraform_init_fails() -> None:
    fake_run, _ = _make_ownership_check_run(init_ok=False)

    with pytest.raises(install.InstallError, match="terraform init"):
        install.check_namespace_ownership(
            terraform_dir=Path("/tf"),
            terraform_state_path=Path("/state/terraform.tfstate"),
            namespace="lakehouse",
            kube_context="my-cluster",
            kubeconfig_path="/home/user/.kube/config",
            adopt_namespace=False,
            run=fake_run,
        )


def test_check_namespace_ownership_passes_the_backend_state_path_to_terraform_init() -> None:
    fake_run, calls = _make_ownership_check_run(state_owns=True)

    install.check_namespace_ownership(
        terraform_dir=Path("/tf"),
        terraform_state_path=Path("/state/terraform.tfstate"),
        namespace="lakehouse",
        kube_context="my-cluster",
        kubeconfig_path="/home/user/.kube/config",
        adopt_namespace=False,
        run=fake_run,
    )

    init_call = next(c for c in calls if c[0] == "terraform" and "init" in c)
    assert "-backend-config=path=/state/terraform.tfstate" in init_call
    assert "-chdir=/tf" in init_call


def test_check_namespace_ownership_targets_the_given_kube_context_and_kubeconfig() -> None:
    fake_run, calls = _make_ownership_check_run(state_owns=False, namespace_exists=False)

    install.check_namespace_ownership(
        terraform_dir=Path("/tf"),
        terraform_state_path=Path("/state/terraform.tfstate"),
        namespace="lakehouse",
        kube_context="my-cluster",
        kubeconfig_path="/some/kubeconfig",
        adopt_namespace=False,
        run=fake_run,
    )

    kubectl_call = next(c for c in calls if c[0] == "kubectl")
    assert kubectl_call == ["kubectl", "--context", "my-cluster", "get", "namespace", "lakehouse"]


def test_run_install_calls_check_namespace_ownership_before_applying_the_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Integration-level check that run_install actually wires the ownership
    check in, and that it runs before platform-up.sh -- not after, where a
    refusal would arrive too late (Terraform would already have applied).
    """
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    calls: list[str] = []

    def fake_check(*, run, **kwargs):
        calls.append("check_namespace_ownership")

    monkeypatch.setattr(install, "check_namespace_ownership", fake_check)

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    def fake_run(command, **kwargs):
        if command[0] == "bash":
            calls.append(Path(command[1]).name)
        return FakeCompleted()

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1", kube_context="my-cluster", assets_dir=assets_dir, work_dir=tmp_path / "work"
    )

    install.run_install(options, run=fake_run)

    assert calls == ["check_namespace_ownership", "platform-up.sh", "deploy-artifacts.sh"]


def test_run_install_rejects_reusing_work_dir_for_a_different_namespace(tmp_path: Path) -> None:
    """Integration-level regression test for the destructive case: an
    explicit --work-dir shared across two `olf install run` invocations
    with different --namespace values must refuse the second one, before
    it ever shells out to anything.
    """
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    work_dir = tmp_path / "work"

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    first_options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        namespace="lakehouse",
        assets_dir=assets_dir,
        work_dir=work_dir,
    )
    install.run_install(first_options, run=lambda *a, **k: FakeCompleted())

    def fail_if_called(*args, **kwargs):
        raise AssertionError("run_install must reject the mismatched target before shelling out to anything")

    second_options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        namespace="analytics",
        assets_dir=assets_dir,
        work_dir=work_dir,
    )

    with pytest.raises(install.InstallError, match="different install target"):
        install.run_install(second_options, run=fail_if_called)


def test_default_work_dir_disambiguates_context_names_that_normalize_identically() -> None:
    # "team/prod" and "team_prod" both sanitize to the same directory-name
    # prefix; the identity hash must still tell them apart.
    a = install.default_work_dir("team/prod", "lakehouse", "/home/user/.kube/config")
    b = install.default_work_dir("team_prod", "lakehouse", "/home/user/.kube/config")
    assert a != b


def test_default_work_dir_sanitizes_unsafe_characters() -> None:
    work_dir = install.default_work_dir(
        "arn:aws:eks:eu-west-1:123:cluster/my cluster", "lakehouse", "/home/user/.kube/config"
    )
    assert " " not in str(work_dir)
    assert ":" not in work_dir.name and ":" not in work_dir.parent.name


# --- manifest-derived deploy inputs -----------------------------------------


def test_repository_and_release_tag_splits_resolved_image_ref() -> None:
    repo, tag = install.repository_and_release_tag(
        "ghcr.io/malon64/openlakeforge/project-code@sha256:" + "a" * 64, "0.1.0-alpha.1"
    )
    assert repo == "ghcr.io/malon64/openlakeforge/project-code"
    assert tag == "0.1.0-alpha.1"


def test_repository_and_release_tag_rejects_malformed_reference() -> None:
    with pytest.raises(install.InstallError):
        install.repository_and_release_tag("not-a-digest-ref", "0.1.0-alpha.1")


def test_profile_env_slim_disables_optional_layers() -> None:
    assert install.profile_env("slim") == {"ENABLE_GOVERNANCE": "false", "ENABLE_ANALYTICS": "false"}


def test_profile_env_full_enables_optional_layers() -> None:
    assert install.profile_env("full") == {"ENABLE_GOVERNANCE": "true", "ENABLE_ANALYTICS": "true"}


def test_profile_env_rejects_unknown_profile() -> None:
    with pytest.raises(install.InstallError, match="unknown --profile"):
        install.profile_env("medium")


# --- canonical_repository ---------------------------------------------------


def test_canonical_repository_strips_tag_and_digest() -> None:
    assert install.canonical_repository("postgres:16-alpine@sha256:" + "b" * 64) == "docker.io/library/postgres"


def test_canonical_repository_expands_docker_hub_org_repo() -> None:
    assert install.canonical_repository("apache/polaris:1.4.0@sha256:" + "d" * 64) == "docker.io/apache/polaris"


def test_canonical_repository_expands_docker_hub_single_segment_name() -> None:
    assert install.canonical_repository("alpine/k8s:1.30.0@sha256:" + "a" * 64) == "docker.io/alpine/k8s"
    assert install.canonical_repository("python:3.12-slim@sha256:" + "a" * 64) == "docker.io/library/python"


def test_canonical_repository_leaves_explicit_registry_unchanged() -> None:
    assert (
        install.canonical_repository("docker.getcollate.io/openmetadata/ingestion-base:1.12.10@sha256:" + "1" * 64)
        == "docker.getcollate.io/openmetadata/ingestion-base"
    )
    assert (
        install.canonical_repository("ghcr.io/malon64/openlakeforge/project-code@sha256:" + "4" * 64)
        == "ghcr.io/malon64/openlakeforge/project-code"
    )


def test_canonical_repository_leaves_already_qualified_docker_hub_unchanged() -> None:
    assert (
        install.canonical_repository("docker.io/bitnamilegacy/redis:7.0.10-debian-11-r4@sha256:" + "3" * 64)
        == "docker.io/bitnamilegacy/redis"
    )


# --- expected_images / check_parity -----------------------------------------


def test_expected_images_slim_excludes_optional_layers() -> None:
    expected = install.expected_images(_manifest(), governance=False, analytics=False)
    assert not any("opensearch" in repo or "superset" in repo for repo in expected)
    assert "docker.io/library/postgres" in expected
    assert "ghcr.io/malon64/openlakeforge/project-code" in expected


def test_expected_images_full_includes_optional_layers() -> None:
    expected = install.expected_images(_manifest(), governance=True, analytics=True)
    assert any("opensearch" in repo for repo in expected)
    assert any("bitnamilegacy/redis" in repo for repo in expected)
    assert "ghcr.io/malon64/openlakeforge/superset" in expected


def test_expected_images_full_does_not_require_the_on_demand_ingestion_image() -> None:
    expected = install.expected_images(_manifest(), governance=True, analytics=True)
    assert not any("ingestion-base" in repo for repo in expected)


def test_expected_images_does_not_require_the_init_only_superset_wait_image() -> None:
    """Regression test: superset_init (infra/helm/values/local/superset.yaml's
    initImage) backs ONLY wait-for-postgres/wait-for-postgres-redis
    initContainers (confirmed against the pinned chart), never a regular
    container -- combined with initContainers being excluded entirely from
    pod_images_from_payload, requiring it here would report a permanent
    false MISSING on every healthy full-profile install.
    """
    expected = install.expected_images(_manifest(), governance=False, analytics=True)
    assert "docker.io/apache/superset" not in expected
    assert "docker.io/bitnamilegacy/redis" in expected


def test_expected_images_accounts_for_every_real_catalog_image() -> None:
    """Regression test for the exact drift class that let polaris_admin_tool
    ship silently unaccounted-for: it was added to
    release/component-catalog.yaml (a one-off Polaris metastore bootstrap
    Job image, in the same category as k8s_bootstrap) without a matching
    update to install.py's profile/exclusion bookkeeping, which would have
    left `olf install verify` never checking it and nothing catching that.

    A new catalog image landing in neither _PROFILE_CATALOG_IMAGES nor
    _INTENTIONALLY_EXCLUDED_CATALOG_IMAGES now fails this test instead of
    silently falling through both.
    """
    catalog = yaml.safe_load((ROOT / "release" / "component-catalog.yaml").read_text())
    catalog_image_names = set(catalog["components"]["images"])

    covered = {name for names in install._PROFILE_CATALOG_IMAGES.values() for name in names}
    excluded = set(install._INTENTIONALLY_EXCLUDED_CATALOG_IMAGES)

    overlap = covered & excluded
    assert not overlap, f"catalog image(s) {sorted(overlap)} are both required and intentionally excluded"

    unaccounted = catalog_image_names - covered - excluded
    assert not unaccounted, (
        f"catalog image(s) {sorted(unaccounted)} are neither in _PROFILE_CATALOG_IMAGES nor "
        "_INTENTIONALLY_EXCLUDED_CATALOG_IMAGES in tools/olf/olf/install.py"
    )

    stale = (covered | excluded) - catalog_image_names
    assert not stale, f"{sorted(stale)} in tools/olf/olf/install.py no longer exist in release/component-catalog.yaml"


def test_expected_images_excludes_skip_repositories() -> None:
    expected = install.expected_images(
        _manifest(),
        governance=False,
        analytics=False,
        skip_repositories=frozenset({"ghcr.io/malon64/openlakeforge/project-code"}),
    )
    assert "ghcr.io/malon64/openlakeforge/project-code" not in expected
    assert "docker.io/library/postgres" in expected


def test_check_parity_reports_matched_missing_mismatched_and_unrecognized() -> None:
    expected = {
        "docker.io/library/postgres": "postgres:16-alpine@sha256:" + "b" * 64,
        "docker.io/trinodb/trino": "trinodb/trino:480@sha256:" + "e" * 64,
        "docker.io/apache/polaris": "apache/polaris:1.4.0@sha256:" + "d" * 64,
    }
    observed = [
        # matches, despite the tag-free/canonical form kubelet actually reports
        {"image_id": "docker.io/library/postgres@sha256:" + "b" * 64},
        {"image_id": "docker.io/trinodb/trino@sha256:" + "0" * 64},  # mismatch
        {"image_id": ""},  # unresolved, ignored
        {"image_id": "unexpected/thing@sha256:" + "9" * 64},  # unrecognized
        # "docker.io/apache/polaris" never observed -> missing
    ]

    report = install.check_parity(expected, observed)

    assert report.matched == ("docker.io/library/postgres",)
    assert report.missing == ("docker.io/apache/polaris",)
    assert len(report.mismatched) == 1
    assert report.mismatched[0].repository == "docker.io/trinodb/trino"
    assert report.mismatched[0].observed == ("docker.io/trinodb/trino@sha256:" + "0" * 64,)
    assert report.unrecognized == ("docker.io/unexpected/thing",)
    assert report.ok(strict=False) is False
    assert report.ok(strict=True) is False


def test_check_parity_skip_repositories_excludes_from_observed_side_too() -> None:
    """--skip-image must fully exclude a repository, not just from `expected`:
    if the skipped image resolves normally (repo@digest, e.g. because the
    runtime genuinely does populate a repo digest for it), it must not
    surface as unrecognized either -- that would defeat the flag's purpose
    of rehearsing without claiming unprovable digest parity for it.
    """
    expected = install.expected_images(
        _manifest(), governance=False, analytics=False, skip_repositories=frozenset({"docker.io/library/postgres"})
    )
    observed = [
        {"image_id": "docker.io/library/postgres@sha256:" + "9" * 64},  # skipped repo, would be "unrecognized"
        {"image_id": "docker.io/apache/polaris@sha256:" + "d" * 64},
    ]

    report = install.check_parity(expected, observed, skip_repositories=frozenset({"docker.io/library/postgres"}))

    assert "docker.io/library/postgres" not in report.unrecognized
    assert "docker.io/library/postgres" not in report.missing


def test_parity_report_ok_ignores_unrecognized_unless_strict() -> None:
    report = install.ParityReport(matched=("postgres",), missing=(), mismatched=(), unrecognized=("some/other-image",))
    assert report.ok(strict=False) is True
    assert report.ok(strict=True) is False


def test_parity_report_ok_false_on_missing_even_without_strict() -> None:
    report = install.ParityReport(matched=(), missing=("polaris",), mismatched=(), unrecognized=())
    assert report.ok(strict=False) is False


def test_run_verify_reads_pod_images_and_checks_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = _manifest()
    from olf import k8s

    monkeypatch.setattr(
        k8s,
        "list_pod_images",
        lambda namespace, *, kube_context=None, kubeconfig_path=None: [
            {"image_id": manifest["catalog"]["components"]["images"]["postgres"]},
            {"image_id": manifest["catalog"]["components"]["images"]["seaweedfs"]},
            {"image_id": manifest["catalog"]["components"]["images"]["polaris"]},
            {"image_id": manifest["catalog"]["components"]["images"]["trino"]},
            {"image_id": manifest["resolved_images"]["project-code"]},
        ],
    )

    report = install.run_verify(
        manifest, namespace="lakehouse", kube_context="kind-openlakeforge-local", governance=False, analytics=False
    )

    assert report.ok(strict=True) is True


# --- run_install orchestration ---------------------------------------------


def _build_release_assets(tmp_path: Path, *, manifest: dict, sign: bool = True) -> Path:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()

    bundle_root_name = "openlakeforge-0.1.0-alpha.1"
    bundle_source = tmp_path / "bundle-source"
    (bundle_source / bundle_root_name / "scripts" / "local" / "stack").mkdir(parents=True)
    stack_dir = bundle_source / bundle_root_name / "scripts" / "local" / "stack"
    for script_name in ("platform-up.sh", "deploy-artifacts.sh"):
        (stack_dir / script_name).write_text("#!/usr/bin/env bash\n")
    bundle_path = assets_dir / "install-bundle.tar.gz"
    with tarfile.open(bundle_path, "w:gz") as archive:
        archive.add(bundle_source / bundle_root_name, arcname=bundle_root_name)

    manifest_path = assets_dir / "component-manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    checksums_entries = []
    for path in sorted(assets_dir.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            checksums_entries.append(f"{digest}  {path.relative_to(assets_dir).as_posix()}\n")
    (assets_dir / "checksums.txt").write_text("".join(checksums_entries))
    if sign:
        (assets_dir / "checksums.txt.bundle").write_text("{}")

    return assets_dir


def test_run_install_drives_phase_scripts_with_manifest_derived_env(tmp_path: Path) -> None:
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)

    commands: list[list[str]] = []
    envs: list[dict[str, str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    def fake_run(command, **kwargs):
        if command[0] == "bash":
            commands.append(command)
            envs.append(kwargs.get("env", {}))
        return FakeCompleted()

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=assets_dir,
        work_dir=tmp_path / "work",
        profile="slim",
    )

    result = install.run_install(options, run=fake_run)

    assert [Path(c[1]).name for c in commands] == ["platform-up.sh", "deploy-artifacts.sh"]
    platform_env = envs[0]
    assert platform_env["FOUNDATION_MODE"] == "existing-cluster"
    assert platform_env["KUBE_CONTEXT"] == "my-cluster"
    assert platform_env["PROJECT_CODE_IMAGE_REPOSITORY"] == "ghcr.io/malon64/openlakeforge/project-code"
    assert platform_env["PROJECT_CODE_IMAGE_TAG"] == "0.1.0-alpha.1"
    assert platform_env["PROJECT_CODE_IMAGE_DIGEST"] == "sha256:" + "4" * 64
    assert platform_env["SUPERSET_IMAGE_REPOSITORY"] == "ghcr.io/malon64/openlakeforge/superset"
    assert platform_env["SUPERSET_IMAGE_TAG"] == "0.1.0-alpha.1"
    assert platform_env["SUPERSET_IMAGE_DIGEST"] == "sha256:" + "5" * 64
    assert platform_env["ENABLE_GOVERNANCE"] == "false"
    assert platform_env["ENABLE_ANALYTICS"] == "false"
    assert result["manifest"] == manifest
    deploy_env = envs[1]
    assert deploy_env["FLOE_IMAGE"] == "ghcr.io/malon64/floe:0.6.11@sha256:" + "6" * 64
    # Terraform's state lives under <work-dir>/state/, never inside the
    # bundle unpack_install_bundle wipes on every run -- see run_install.
    assert platform_env["TERRAFORM_STATE_PATH"] == str(tmp_path / "work" / "state" / "terraform.tfstate")


def test_run_install_passes_adopt_namespace_through_to_the_ownership_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--adopt-namespace is decided in Python (check_namespace_ownership),
    not passed to platform-up.sh as an env var -- see AGENTS.md #4 ("Python
    for behaviour, shell for structure").
    """
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    received: dict[str, object] = {}

    def fake_check(*, adopt_namespace, **kwargs):
        received["adopt_namespace"] = adopt_namespace

    monkeypatch.setattr(install, "check_namespace_ownership", fake_check)

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=assets_dir,
        work_dir=tmp_path / "work",
        adopt_namespace=True,
    )

    install.run_install(options, run=lambda *a, **k: FakeCompleted())

    assert received["adopt_namespace"] is True


def test_run_install_creates_separate_state_and_config_directories(tmp_path: Path) -> None:
    """bundle/ is wiped and re-extracted every run; state/ and config/ never
    are -- confirm run_install actually creates both up front rather than
    relying on some later step to do it implicitly.
    """
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    work_dir = tmp_path / "work"

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1", kube_context="my-cluster", assets_dir=assets_dir, work_dir=work_dir
    )

    install.run_install(options, run=lambda *a, **k: FakeCompleted())

    assert (work_dir / "state").is_dir()
    assert (work_dir / "config").is_dir()


def test_run_install_resolves_a_relative_work_dir_to_an_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative --work-dir must not be handed to platform-up.sh as-is:
    TERRAFORM_STATE_PATH becomes a Terraform -backend-config="path=..."
    value, which Terraform resolves relative to the ROOT MODULE's directory
    (the bundle's -chdir target) when given a relative path -- not this
    process's own cwd, where a relative --work-dir would otherwise be
    meaningful.
    """
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    envs: list[dict[str, str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    def fake_run(command, **kwargs):
        if command[0] == "bash":
            envs.append(kwargs.get("env", {}))
        return FakeCompleted()

    monkeypatch.chdir(tmp_path)
    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=assets_dir,
        work_dir=Path("relative-work-dir"),
    )

    result = install.run_install(options, run=fake_run)

    assert result["work_dir"].is_absolute()
    assert result["work_dir"] == (tmp_path / "relative-work-dir").resolve()
    assert Path(envs[0]["TERRAFORM_STATE_PATH"]).is_absolute()


def test_run_install_resolves_a_relative_assets_dir_to_an_absolute_manifest_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cli.py prints result['manifest_path'] in the advertised 'olf install
    verify' follow-up command. A relative --assets-dir is meaningful for
    THIS invocation (against this cwd), but the printed command may be
    copy-pasted and run later from a different directory, where the same
    relative value would resolve to a different file or none at all.
    """
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    monkeypatch.chdir(tmp_path)
    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=Path("assets"),  # relative form of assets_dir, since cwd is now tmp_path
        work_dir=tmp_path / "work",
    )

    result = install.run_install(options, run=lambda *a, **k: FakeCompleted())

    assert result["manifest_path"].is_absolute()
    assert result["manifest_path"] == (assets_dir / "component-manifest.json").resolve()


def test_run_install_defaults_floe_image_to_empty_when_catalog_lacks_it(tmp_path: Path) -> None:
    """Regression test for manifests from a catalog predating floe's
    registration: run_install must not raise a KeyError, and floe-manifest.sh's
    own digest-pinned default (release/component-catalog.yaml) takes over --
    passing an empty string is bash's own "unset" for `${FLOE_IMAGE:-default}`.
    """
    manifest = _manifest()
    del manifest["catalog"]["components"]["images"]["floe"]
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    envs: list[dict[str, str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    def fake_run(command, **kwargs):
        if command[0] == "bash":
            envs.append(kwargs.get("env", {}))
        return FakeCompleted()

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1", kube_context="my-cluster", assets_dir=assets_dir, work_dir=tmp_path / "work"
    )

    install.run_install(options, run=fake_run)

    assert envs[0]["FLOE_IMAGE"] == ""


def test_resolve_kubeconfig_path_resolves_a_single_file(tmp_path: Path) -> None:
    kubeconfig = tmp_path / "cluster.yaml"
    kubeconfig.write_text("kind: Config\n")

    assert install.resolve_kubeconfig_path(str(kubeconfig)) == str(kubeconfig.resolve())


def test_resolve_kubeconfig_path_rejects_a_pathsep_separated_list(tmp_path: Path) -> None:
    """KUBECONFIG (and, by extension, --kubeconfig) can legitimately be an
    os.pathsep-separated LIST of files that kubectl merges into one logical
    view -- see
    https://kubernetes.io/docs/concepts/configuration/organize-cluster-access-kubeconfig/#the-kubeconfig-environment-variable.
    Terraform's kubernetes/helm providers only take a single config_path, so
    treating the whole list as one filesystem path would silently resolve
    to a nonexistent file and lose every context/cert merged in from the
    other files in the list. Must refuse instead of guessing.
    """
    first = tmp_path / "a.yaml"
    second = tmp_path / "b.yaml"
    multi_path_value = f"{first}{os.pathsep}{second}"

    with pytest.raises(install.InstallError, match="separated list of files"):
        install.resolve_kubeconfig_path(multi_path_value)


def test_resolve_kubeconfig_path_rejects_a_multi_file_ambient_kubeconfig_env_var(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "a.yaml"
    second = tmp_path / "b.yaml"
    monkeypatch.setenv("KUBECONFIG", f"{first}{os.pathsep}{second}")

    with pytest.raises(install.InstallError, match="KUBECONFIG"):
        install.resolve_kubeconfig_path(None)


def test_run_install_rejects_a_multi_file_kubeconfig_before_any_network_or_cosign_work(
    tmp_path: Path,
) -> None:
    first = tmp_path / "a.yaml"
    second = tmp_path / "b.yaml"

    def fail_if_called(*args, **kwargs):
        raise AssertionError("run_install must reject a multi-file --kubeconfig before shelling out to anything")

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=tmp_path / "unused-assets",
        work_dir=tmp_path / "work",
        kubeconfig_path=f"{first}{os.pathsep}{second}",
    )

    with pytest.raises(install.InstallError, match="separated list of files"):
        install.run_install(options, run=fail_if_called)


def test_run_install_resolves_a_relative_kubeconfig_to_an_absolute_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative --kubeconfig must not be handed to platform-up.sh as-is:
    Terraform resolves it relative to its OWN -chdir target
    (<bundle_root>/infra/terraform/environments/local), not the caller's
    cwd where kubectl (and the user) resolved it -- so a relative value
    would silently point Terraform at a different, likely nonexistent file.
    """
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    envs: list[dict[str, str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    def fake_run(command, **kwargs):
        if command[0] == "bash":
            envs.append(kwargs.get("env", {}))
        return FakeCompleted()

    monkeypatch.chdir(tmp_path)
    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=assets_dir,
        work_dir=tmp_path / "work",
        kubeconfig_path="./relative-cluster.yaml",
    )

    install.run_install(options, run=fake_run)

    resolved = envs[0]["KUBECONFIG_PATH"]
    assert Path(resolved).is_absolute()
    assert resolved == str((tmp_path / "relative-cluster.yaml").resolve())


def test_run_install_sets_kubeconfig_env_var_to_the_resolved_kubeconfig(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The phase scripts' own configure_deployment_scope re-derives KUBECONFIG
    from KUBECONFIG_PATH before their first kubectl call, but run_install
    must not rely on that alone: it copies this process's own os.environ
    (which may carry an unrelated, stale KUBECONFIG inherited from the
    caller's shell) into the phase-script env, so KUBECONFIG must be
    overridden explicitly too -- otherwise anything that reads it before
    configure_deployment_scope runs would target the wrong cluster.
    """
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    envs: list[dict[str, str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    def fake_run(command, **kwargs):
        if command[0] == "bash":
            envs.append(kwargs.get("env", {}))
        return FakeCompleted()

    stale_kubeconfig = tmp_path / "stale-ambient-kubeconfig.yaml"
    stale_kubeconfig.write_text("stale")
    monkeypatch.setenv("KUBECONFIG", str(stale_kubeconfig))

    chosen_kubeconfig = tmp_path / "chosen-cluster.yaml"
    chosen_kubeconfig.write_text("chosen")
    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=assets_dir,
        work_dir=tmp_path / "work",
        kubeconfig_path=str(chosen_kubeconfig),
    )

    install.run_install(options, run=fake_run)

    assert envs[0]["KUBECONFIG"] == str(chosen_kubeconfig.resolve())
    assert envs[0]["KUBECONFIG"] == envs[0]["KUBECONFIG_PATH"]


def test_run_install_defaults_storage_values_override_env_var_to_empty(tmp_path: Path) -> None:
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    envs: list[dict[str, str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    def fake_run(command, **kwargs):
        if command[0] == "bash":
            envs.append(kwargs.get("env", {}))
        return FakeCompleted()

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1", kube_context="my-cluster", assets_dir=assets_dir, work_dir=tmp_path / "work"
    )

    install.run_install(options, run=fake_run)

    assert envs[0]["SEAWEEDFS_OVERRIDE_VALUES_FILE"] == ""


def test_run_install_persists_storage_values_override_into_work_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The override file's CONTENT is copied into --work-dir rather than the
    original path being passed through: this survives olf install's own
    bundle re-extraction (unpack_install_bundle deletes and re-extracts the
    bundle subtree on every run) and, more importantly, survives a LATER
    run against the same target that omits --storage-values-override
    entirely -- see test_run_install_reuses_a_previously_persisted_storage_values_override.
    """
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    override_file = tmp_path / "overrides" / "seaweedfs.yaml"
    override_file.parent.mkdir()
    override_content = "master:\n  data:\n    type: persistentVolumeClaim\n"
    override_file.write_text(override_content)
    envs: list[dict[str, str]] = []
    work_dir = tmp_path / "work"

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    def fake_run(command, **kwargs):
        if command[0] == "bash":
            envs.append(kwargs.get("env", {}))
        return FakeCompleted()

    monkeypatch.chdir(tmp_path)
    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=assets_dir,
        work_dir=work_dir,
        storage_values_override=Path("overrides/seaweedfs.yaml"),
    )

    install.run_install(options, run=fake_run)

    resolved = envs[0]["SEAWEEDFS_OVERRIDE_VALUES_FILE"]
    assert Path(resolved).is_absolute()
    # Under <work-dir>/config/, never <work-dir>/bundle/ -- see run_install.
    assert Path(resolved).parent == work_dir / "config"
    assert Path(resolved).read_text() == override_content


def test_run_install_reads_the_storage_values_override_before_extracting_the_bundle(tmp_path: Path) -> None:
    """Regression test: if the override were read AFTER unpack_install_bundle
    (as it originally was), pointing --storage-values-override at a path
    inside <work-dir>/bundle/ -- the one place the docs say never to put it
    -- would silently read back whatever the release's own re-extraction put
    there instead of the caller's actual file, appearing to succeed while
    persisting the wrong (possibly ephemeral-storage) values. Placing the
    override file inside the (not-yet-extracted) bundle directory here
    proves the read happens first: unpack_install_bundle's
    _clear_stale_bundle_content would delete this file before extraction if
    it ran first, since it is not part of the release archive.
    """
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    work_dir = tmp_path / "work"
    bundle_dir = work_dir / "bundle"
    bundle_dir.mkdir(parents=True)
    override_content = "master:\n  data:\n    type: persistentVolumeClaim\n"
    override_file_inside_bundle = bundle_dir / "my-seaweedfs-override.yaml"
    override_file_inside_bundle.write_text(override_content)
    envs: list[dict[str, str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    def fake_run(command, **kwargs):
        if command[0] == "bash":
            envs.append(kwargs.get("env", {}))
        return FakeCompleted()

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=assets_dir,
        work_dir=work_dir,
        storage_values_override=override_file_inside_bundle,
    )

    install.run_install(options, run=fake_run)

    resolved = envs[0]["SEAWEEDFS_OVERRIDE_VALUES_FILE"]
    assert Path(resolved).read_text() == override_content


def test_run_install_reuses_a_previously_persisted_storage_values_override(tmp_path: Path) -> None:
    """Regression test: a later `olf install run` (e.g. an upgrade) against
    the same --work-dir that omits --storage-values-override must NOT
    silently drop a previously selected override -- that would revert
    SeaweedFS to the bundle's emptyDir values, which can fail the Helm
    upgrade on an immutable PVC field or, worse, succeed and quietly move
    data back onto ephemeral storage.
    """
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    work_dir = tmp_path / "work"
    config_dir = work_dir / "config"
    config_dir.mkdir(parents=True)
    persisted_override_content = "master:\n  data:\n    type: persistentVolumeClaim\n"
    (config_dir / "storage-values-override.yaml").write_text(persisted_override_content)
    envs: list[dict[str, str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    def fake_run(command, **kwargs):
        if command[0] == "bash":
            envs.append(kwargs.get("env", {}))
        return FakeCompleted()

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1", kube_context="my-cluster", assets_dir=assets_dir, work_dir=work_dir
    )

    install.run_install(options, run=fake_run)

    resolved = envs[0]["SEAWEEDFS_OVERRIDE_VALUES_FILE"]
    assert resolved == str(config_dir / "storage-values-override.yaml")
    assert Path(resolved).read_text() == persisted_override_content


def test_run_install_updates_the_persisted_storage_values_override_when_a_new_one_is_passed(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    work_dir = tmp_path / "work"
    config_dir = work_dir / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "storage-values-override.yaml").write_text("old-override\n")
    new_override_file = tmp_path / "new-seaweedfs-override.yaml"
    new_override_file.write_text("new-override\n")
    envs: list[dict[str, str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    def fake_run(command, **kwargs):
        if command[0] == "bash":
            envs.append(kwargs.get("env", {}))
        return FakeCompleted()

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=assets_dir,
        work_dir=work_dir,
        storage_values_override=new_override_file,
    )

    install.run_install(options, run=fake_run)

    resolved = envs[0]["SEAWEEDFS_OVERRIDE_VALUES_FILE"]
    assert Path(resolved).read_text() == "new-override\n"


def test_run_install_rejects_a_missing_storage_values_override(tmp_path: Path) -> None:
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=assets_dir,
        work_dir=tmp_path / "work",
        storage_values_override=tmp_path / "does-not-exist.yaml",
    )

    with pytest.raises(install.InstallError, match="storage-values-override"):
        install.run_install(options, run=lambda *a, **k: FakeCompleted())


def test_run_install_skip_digest_pin_leaves_digest_env_vars_empty(tmp_path: Path) -> None:
    """Rehearsal-only escape hatch: a locally built, never-pushed image has
    no registry-assigned digest to pin Phase 2 to at all, so
    --skip-digest-pin must fall Phase 2 back to patching by tag (empty
    *_IMAGE_DIGEST, which deploy-artifacts.sh's existing `[[ -n ... ]]`
    guard already treats as "not set").
    """
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    envs: list[dict[str, str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    def fake_run(command, **kwargs):
        if command[0] == "bash":
            envs.append(kwargs.get("env", {}))
        return FakeCompleted()

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=assets_dir,
        work_dir=tmp_path / "work",
        skip_digest_pin=True,
    )

    install.run_install(options, run=fake_run)

    platform_env = envs[0]
    assert platform_env["PROJECT_CODE_IMAGE_DIGEST"] == ""
    assert platform_env["SUPERSET_IMAGE_DIGEST"] == ""
    assert platform_env["PROJECT_CODE_IMAGE_TAG"] == "0.1.0-alpha.1"


def test_run_install_skips_artifacts_when_requested(tmp_path: Path) -> None:
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)
    commands: list[list[str]] = []

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    def fake_run(command, **kwargs):
        if command[0] == "bash":
            commands.append(command)
        return FakeCompleted()

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=assets_dir,
        work_dir=tmp_path / "work",
        skip_artifacts=True,
    )
    install.run_install(options, run=fake_run)

    assert [Path(c[1]).name for c in commands] == ["platform-up.sh"]


def test_run_install_requires_signature_bundle_unless_skipped(tmp_path: Path) -> None:
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest, sign=False)

    class FakeCompleted:
        returncode = 0
        stdout = "fake-cluster-uid"
        stderr = ""

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=assets_dir,
        work_dir=tmp_path / "work",
    )
    with pytest.raises(install.InstallError, match="checksums.txt.bundle"):
        install.run_install(options, run=lambda *a, **k: FakeCompleted())


def test_run_install_fails_when_phase_script_exits_nonzero(tmp_path: Path) -> None:
    manifest = _manifest()
    assets_dir = _build_release_assets(tmp_path, manifest=manifest)

    class FakeCompleted:
        def __init__(self, returncode: int):
            self.returncode = returncode
            self.stdout = "fake-cluster-uid" if returncode == 0 else ""
            self.stderr = "boom" if returncode != 0 else ""

    def fake_run(command, **kwargs):
        # check_work_dir_target_identity's and check_namespace_ownership's
        # terraform/kubectl calls succeed (empty namespace, nothing to check
        # ownership against); only the actual phase script (bash
        # platform-up.sh) fails.
        if command[0] == "bash":
            return FakeCompleted(1)
        return FakeCompleted(0)

    options = install.InstallOptions(
        tag="v0.1.0-alpha.1",
        kube_context="my-cluster",
        assets_dir=assets_dir,
        work_dir=tmp_path / "work",
        skip_signature_verification=True,
    )
    with pytest.raises(install.InstallError, match="platform-up.sh failed"):
        install.run_install(options, run=fake_run)
