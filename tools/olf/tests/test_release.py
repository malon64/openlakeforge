import json
from pathlib import Path

import pytest
import yaml

from olf import release

ROOT = Path(__file__).parents[3]


def _write_catalog(tmp_path: Path, **overrides) -> Path:
    catalog = {
        "apiVersion": "openlakeforge.io/v1alpha1",
        "kind": "ComponentCatalog",
        "metadata": {"name": "openlakeforge"},
        "distribution": {"version": "0.1.0-alpha.1", "release_tag_policy": "immutable-semver"},
        "components": {
            "terraform": {"required_version": ">= 1.6.0", "providers": {"hashicorp/aws": "5.100.0"}},
            "python": {
                "project_code_lock": "images/project-code/requirements.lock",
                "tooling_lock": "tools/olf/uv.lock",
            },
            "images": {
                "project_code_base": "python:3.12-slim@sha256:" + "a" * 64,
            },
            "actions": {"actions/checkout": "a" * 40},
        },
    }
    catalog.update(overrides)
    path = tmp_path / "component-catalog.yaml"
    path.write_text(yaml.safe_dump(catalog))
    return path


def test_load_catalog_reads_real_repo_catalog() -> None:
    catalog = release.load_catalog(ROOT / "release/component-catalog.yaml")
    assert release.catalog_version(catalog) == "0.1.0-alpha.1"


def test_load_catalog_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(release.ReleaseError):
        release.load_catalog(tmp_path / "missing.yaml")


def test_load_catalog_rejects_bad_version(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, distribution={"version": "not-a-version"})
    with pytest.raises(release.ReleaseError):
        release.load_catalog(path)


def test_tag_for_version_and_back() -> None:
    assert release.tag_for_version("0.1.0-alpha.1") == "v0.1.0-alpha.1"
    assert release.version_for_tag("v0.1.0-alpha.1") == "0.1.0-alpha.1"
    assert release.version_for_tag("0.1.0-alpha.1") == "0.1.0-alpha.1"


def test_build_manifest_includes_catalog_and_digests(tmp_path: Path) -> None:
    catalog = release.load_catalog(_write_catalog(tmp_path))
    manifest = release.build_manifest(
        catalog,
        git_sha="deadbeef",
        image_digests={"project-code": "ghcr.io/malon64/openlakeforge/project-code@sha256:" + "b" * 64},
    )
    assert manifest["distribution"]["version"] == "0.1.0-alpha.1"
    assert manifest["distribution"]["tag"] == "v0.1.0-alpha.1"
    assert manifest["distribution"]["git_sha"] == "deadbeef"
    assert manifest["catalog"] == catalog
    assert manifest["resolved_images"]["project-code"].endswith("b" * 64)


def test_render_manifest_json_round_trips(tmp_path: Path) -> None:
    catalog = release.load_catalog(_write_catalog(tmp_path))
    manifest = release.build_manifest(catalog, git_sha="deadbeef")
    rendered = release.render_manifest(manifest, fmt="json")
    assert json.loads(rendered) == manifest


def test_render_manifest_rejects_unknown_format(tmp_path: Path) -> None:
    catalog = release.load_catalog(_write_catalog(tmp_path))
    manifest = release.build_manifest(catalog, git_sha="deadbeef")
    with pytest.raises(release.ReleaseError):
        release.render_manifest(manifest, fmt="toml")


def test_compute_checksums_is_deterministic_and_excludes_itself(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("world")
    (tmp_path / "checksums.txt").write_text("stale")
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "c.txt").write_text("nested")

    entries = release.compute_checksums(tmp_path)
    paths = [path for path, _digest in entries]
    assert paths == ["a.txt", "b.txt", "nested/c.txt"]

    entries_again = release.compute_checksums(tmp_path)
    assert entries == entries_again


def test_write_checksums_produces_sha256sum_compatible_file(tmp_path: Path) -> None:
    (tmp_path / "asset.txt").write_text("payload")
    out = release.write_checksums(tmp_path)
    content = out.read_text()
    assert content.strip().split("  ") == [
        __import__("hashlib").sha256(b"payload").hexdigest(),
        "asset.txt",
    ]


def test_render_compatibility_matrix_includes_catalog_values(tmp_path: Path) -> None:
    catalog = release.load_catalog(_write_catalog(tmp_path))
    rendered = release.render_compatibility_matrix(catalog, tmp_path)
    assert "hashicorp/aws" in rendered
    assert "5.100.0" in rendered
    assert "python_project_code_base" not in rendered  # sanity: no stray key names leak in


def test_render_compatibility_matrix_helm_charts_read_from_terraform_modules_not_catalog(
    tmp_path: Path,
) -> None:
    """The Helm charts table has no catalog counterpart to keep in sync -- it
    is read directly from each Terraform module's own `chart_version`
    variable default.
    """
    catalog = release.load_catalog(_write_catalog(tmp_path))
    module_dir = tmp_path / "infra/terraform/modules/query/trino"
    module_dir.mkdir(parents=True)
    (module_dir / "variables.tf").write_text(
        'variable "namespace" {\n  type = string\n}\n\n'
        'variable "chart_version" {\n  type    = string\n  default = "1.42.2"\n}\n'
    )

    rendered = release.render_compatibility_matrix(catalog, tmp_path)

    assert "## Helm charts" in rendered
    assert "| trino | 1.42.2 |" in rendered


def test_render_compatibility_matrix_omits_per_root_table_with_no_lockfiles(tmp_path: Path) -> None:
    catalog = release.load_catalog(_write_catalog(tmp_path))
    rendered = release.render_compatibility_matrix(catalog, tmp_path)
    assert "Terraform providers by root" not in rendered


def test_render_compatibility_matrix_per_root_table_is_read_from_lockfiles_not_catalog(tmp_path: Path) -> None:
    """The per-root table has no catalog counterpart to keep in sync -- it is
    read directly from each `.terraform.lock.hcl` under infra/terraform.
    """
    catalog = release.load_catalog(_write_catalog(tmp_path))
    lock_dir = tmp_path / "infra/terraform/environments/demo"
    lock_dir.mkdir(parents=True)
    (lock_dir / ".terraform.lock.hcl").write_text(
        'provider "registry.terraform.io/hashicorp/example" {\n  version = "1.2.3"\n}\n'
    )

    rendered = release.render_compatibility_matrix(catalog, tmp_path)

    assert "Terraform providers by root" in rendered
    assert "infra/terraform/environments/demo/.terraform.lock.hcl" in rendered
    assert "1.2.3" in rendered


def test_render_compatibility_matrix_exposes_per_root_provider_drift() -> None:
    """The real repo's infra/terraform/environments/local root locks
    hashicorp/helm at 3.1.1, distinct from the tracked 3.2.0 -- both must be
    visible, in the right table.
    """
    catalog = release.load_catalog(ROOT / "release/component-catalog.yaml")
    rendered = release.render_compatibility_matrix(catalog, ROOT)

    assert "Terraform providers by root" in rendered
    local_root = "infra/terraform/environments/local/.terraform.lock.hcl"
    assert local_root in rendered

    # Extract the hashicorp/helm row from the per-root table (not the tracked-
    # version table above it, which has an identically-prefixed row) and
    # confirm the local column reads 3.1.1, not the tracked 3.2.0 -- parsed
    # structurally, not just substring search, since "3.2.0" and "3.1.1" both
    # appear elsewhere in the document.
    per_root_section = rendered.split("### Terraform providers by root", 1)[1]
    header = next(line for line in per_root_section.splitlines() if line.startswith("| Provider | infra/"))
    columns = [c.strip() for c in header.strip("|").split("|")]
    local_index = columns.index(local_root)
    helm_row = next(line for line in per_root_section.splitlines() if line.startswith("| hashicorp/helm |"))
    cells = [c.strip() for c in helm_row.strip("|").split("|")]
    assert cells[local_index] == "3.1.1"


def test_terraform_lock_provider_versions_parses_real_local_lockfile() -> None:
    versions = release._terraform_lock_provider_versions(
        ROOT / "infra/terraform/environments/local/.terraform.lock.hcl"
    )
    assert versions["hashicorp/helm"] == "3.1.1"
    assert versions["hashicorp/kubernetes"] == "2.38.0"


def test_helm_chart_versions_from_terraform_modules_matches_real_repo() -> None:
    versions = release._helm_chart_versions_from_terraform_modules(ROOT)
    assert versions["trino"] == "1.42.2"
    assert versions["seaweedfs"] == "4.23.0"
    # The paired dependencies chart, from deps_chart_version in the same module.
    assert versions["openmetadata"] == "1.12.10"
    assert versions["openmetadata-dependencies"] == "1.12.10"


def test_helm_chart_versions_from_terraform_modules_ignores_variables_without_defaults(
    tmp_path: Path,
) -> None:
    module_dir = tmp_path / "infra/terraform/modules/query/trino"
    module_dir.mkdir(parents=True)
    (module_dir / "variables.tf").write_text('variable "chart_version" {\n  type = string\n}\n')
    assert release._helm_chart_versions_from_terraform_modules(tmp_path) == {}


def test_chart_versions_match_deployment_wrappers_flags_cached_chart_drift(tmp_path: Path) -> None:
    trino_module = tmp_path / "infra/terraform/modules/query/trino"
    dagster_module = tmp_path / "infra/terraform/modules/orchestration/dagster"
    trino_module.mkdir(parents=True)
    dagster_module.mkdir(parents=True)
    (trino_module / "variables.tf").write_text(
        'variable "chart_version" {\n  default = "1.42.2"\n}\n'
    )
    (dagster_module / "variables.tf").write_text(
        'variable "chart_version" {\n  default = "1.13.6"\n}\n'
    )
    for path, contents in {
        "scripts/local/stack/platform-up.sh": 'TRINO_CHART_VERSION="${TRINO_CHART_VERSION:-9.9.9}"\n',
        "scripts/azure/stack/platform-up.sh": (
            'TRINO_CHART_VERSION="${TRINO_CHART_VERSION:-1.42.2}"\n'
            'DAGSTER_CHART_VERSION="${DAGSTER_CHART_VERSION:-1.13.6}"\n'
        ),
        "scripts/aws/stack/platform-up.sh": (
            'TRINO_CHART_VERSION="${TRINO_CHART_VERSION:-1.42.2}"\n'
            'DAGSTER_CHART_VERSION="${DAGSTER_CHART_VERSION:-1.13.6}"\n'
        ),
    }.items():
        wrapper_path = tmp_path / path
        wrapper_path.parent.mkdir(parents=True, exist_ok=True)
        wrapper_path.write_text(contents)

    result = release._check_chart_versions_match_deployment_wrappers(tmp_path)

    assert not result.ok
    assert "TRINO_CHART_VERSION='9.9.9'" in result.detail


def test_chart_versions_match_deployment_wrappers_passes_on_real_repo() -> None:
    result = release._check_chart_versions_match_deployment_wrappers(ROOT)
    assert result.ok, result.detail


def test_run_release_check_passes_on_real_repo_catalog() -> None:
    report = release.run_release_check(ROOT, tag="v0.1.0-alpha.1")
    assert report.ok, report.render()


def test_run_release_check_fails_on_tag_mismatch() -> None:
    report = release.run_release_check(ROOT, tag="v9.9.9-alpha.9")
    assert not report.ok
    assert any("tag" in result.name for result in report.results if not result.ok)


def test_run_release_check_without_tag_only_validates_catalog_shape() -> None:
    report = release.run_release_check(ROOT)
    version_result = next(r for r in report.results if "valid alpha semver" in r.name)
    assert version_result.ok


def test_check_terraform_required_versions_match_real_catalog() -> None:
    catalog = release.load_catalog(ROOT / "release/component-catalog.yaml")
    result = release._check_terraform_required_versions_match_catalog(ROOT, catalog)
    assert result.ok, result.detail


def test_check_terraform_required_versions_flags_catalog_drift(tmp_path: Path) -> None:
    root_dir = tmp_path / "infra/terraform/environments/demo"
    root_dir.mkdir(parents=True)
    (root_dir / "main.tf").write_text('terraform {\n  required_version = ">= 1.9.0"\n}\n')

    catalog = {"components": {"terraform": {"required_version": ">= 1.6.0"}}}
    result = release._check_terraform_required_versions_match_catalog(tmp_path, catalog)

    assert not result.ok
    assert "environments/demo/main.tf" in result.detail
    assert "terraform='>= 1.9.0'" in result.detail


def test_check_terraform_required_versions_requires_each_root_to_declare_a_constraint(
    tmp_path: Path,
) -> None:
    root_dir = tmp_path / "infra/terraform/environments/demo"
    root_dir.mkdir(parents=True)
    (root_dir / "main.tf").write_text("terraform {\n  required_providers {}\n}\n")

    catalog = {"components": {"terraform": {"required_version": ">= 1.6.0"}}}
    result = release._check_terraform_required_versions_match_catalog(tmp_path, catalog)

    assert not result.ok
    assert "has no required_version" in result.detail


def test_check_terraform_required_versions_requires_a_terraform_block_in_every_root(
    tmp_path: Path,
) -> None:
    root_dir = tmp_path / "infra/terraform/environments/demo"
    root_dir.mkdir(parents=True)
    (root_dir / "main.tf").write_text('locals { name = "demo" }\n')

    catalog = {"components": {"terraform": {"required_version": ">= 1.6.0"}}}
    result = release._check_terraform_required_versions_match_catalog(tmp_path, catalog)

    assert not result.ok
    assert "environments/demo: no terraform block" in result.detail


def test_provider_using_terraform_roots_require_tracked_lockfiles(tmp_path: Path) -> None:
    root_dir = tmp_path / "infra/terraform/environments/demo"
    root_dir.mkdir(parents=True)
    (root_dir / "main.tf").write_text(
        'terraform {\n  required_providers {\n    example = { source = "hashicorp/example" }\n  }\n}\n'
    )

    result = release._check_provider_using_terraform_roots_have_lockfiles(tmp_path)

    assert not result.ok
    assert "infra/terraform/environments/demo/.terraform.lock.hcl" in result.detail


def test_provider_using_terraform_roots_with_lockfiles_passes() -> None:
    result = release._check_provider_using_terraform_roots_have_lockfiles(ROOT)
    assert result.ok, result.detail


def test_terraform_lock_provider_versions_ignores_nondefault_registry_provider(tmp_path: Path) -> None:
    lock_path = tmp_path / ".terraform.lock.hcl"
    lock_path.write_text('provider "registry.example.com/acme/example" {\n  version = "1.2.3"\n}\n')
    versions = release._terraform_lock_provider_versions(lock_path)
    assert versions == {"registry.example.com/acme/example": "1.2.3"}


def test_check_images_digest_pinned_flags_missing_digest() -> None:
    catalog = {"components": {"images": {"bad": "python:3.12-slim"}}}
    result = release._check_images_digest_pinned(catalog)
    assert not result.ok
    assert "bad" in result.detail


def test_check_images_match_deployment_sources_passes_on_real_repo_catalog() -> None:
    catalog = release.load_catalog(ROOT / "release/component-catalog.yaml")
    result = release._check_images_match_deployment_sources(ROOT, catalog)
    assert result.ok, result.detail


def test_check_images_match_deployment_sources_flags_a_stale_catalog_digest(tmp_path: Path) -> None:
    """A Helm values file's digest changing without the matching catalog
    update must be caught -- neither _check_images_digest_pinned (validates
    only the catalog's own shape) nor check-components.sh's Helm-values loop
    (validates only that some digest is present) compares the two.
    """
    values_dir = tmp_path / "infra/helm/values/local"
    values_dir.mkdir(parents=True)
    (values_dir / "trino.yaml").write_text(
        'image:\n  repository: trinodb/trino\n  tag: "480@sha256:' + "b" * 64 + '"\n'
    )

    catalog = {"components": {"images": {"trino": "trinodb/trino:480@sha256:" + "a" * 64}}}
    result = release._check_images_match_deployment_sources(tmp_path, catalog)

    assert not result.ok
    assert "trino" in result.detail
    assert "b" * 64 in result.detail


def test_check_images_match_deployment_sources_ignores_build_only_images() -> None:
    """project_code_base/superset_base are Dockerfile-only build inputs with
    no Helm values counterpart -- absence from infra/helm/values must not be
    flagged as drift.
    """
    catalog = release.load_catalog(ROOT / "release/component-catalog.yaml")
    result = release._check_images_match_deployment_sources(ROOT, catalog)
    assert result.ok, result.detail


def test_check_images_match_deployment_sources_catches_a_tag_bump_the_catalog_missed(
    tmp_path: Path,
) -> None:
    """The scenario from the review: bumping a deployed image to a new tag
    AND digest (e.g. Trino 480 -> 481) without updating the catalog removes
    every occurrence of the catalog's old tag from the deployment source.
    Matching against a registered file/field (not a substring search for the
    old tag) still catches this -- it reads whatever the field currently
    contains, not what the catalog remembers.
    """
    values_dir = tmp_path / "infra/helm/values/local"
    values_dir.mkdir(parents=True)
    (values_dir / "trino.yaml").write_text(
        'image:\n  repository: trinodb/trino\n  tag: "481@sha256:' + "b" * 64 + '"\n'
    )

    catalog = {"components": {"images": {"trino": "trinodb/trino:480@sha256:" + "a" * 64}}}
    result = release._check_images_match_deployment_sources(tmp_path, catalog)

    assert not result.ok
    assert "480" in result.detail
    assert "481" in result.detail


def test_check_images_match_deployment_sources_catches_a_repository_override(
    tmp_path: Path,
) -> None:
    """A mirror override with an unchanged tag and digest is still drift."""
    values_dir = tmp_path / "infra/helm/values/local"
    values_dir.mkdir(parents=True)
    digest = "a" * 64
    (values_dir / "polaris.yaml").write_text(
        'image:\n  repository: mirror.example.com/apache/polaris\n  tag: "1.4.0@sha256:'
        + digest
        + '"\n'
    )

    catalog = {
        "components": {"images": {"polaris": "apache/polaris:1.4.0@sha256:" + digest}}
    }
    result = release._check_images_match_deployment_sources(tmp_path, catalog)

    assert not result.ok
    assert "apache/polaris" in result.detail
    assert "mirror.example.com/apache/polaris" in result.detail


def test_check_images_match_deployment_sources_requires_a_complete_split_reference(
    tmp_path: Path,
) -> None:
    """Split Helm values must pin the repository as well as tag and digest."""
    values_dir = tmp_path / "infra/helm/values/local"
    values_dir.mkdir(parents=True)
    digest = "a" * 64
    (values_dir / "trino.yaml").write_text('image:\n  tag: "480@sha256:' + digest + '"\n')

    catalog = {"components": {"images": {"trino": "trinodb/trino:480@sha256:" + digest}}}
    result = release._check_images_match_deployment_sources(tmp_path, catalog)

    assert not result.ok
    assert "no complete image reference" in result.detail


def test_check_images_match_deployment_sources_flags_an_unregistered_image(tmp_path: Path) -> None:
    """A catalog image with no entry in _IMAGE_DEPLOYMENT_SOURCES or
    _BUILD_ONLY_IMAGES must fail loudly, not silently skip -- silently
    skipping is exactly how the tag-bump case above went unnoticed before.
    """
    catalog = {"components": {"images": {"brand_new_image": "example/new:1.0@sha256:" + "a" * 64}}}
    result = release._check_images_match_deployment_sources(tmp_path, catalog)

    assert not result.ok
    assert "brand_new_image" in result.detail
    assert "no registered deployment source" in result.detail


def test_check_images_match_deployment_sources_flags_every_postgres_terraform_reference(
    tmp_path: Path,
) -> None:
    postgres_dir = tmp_path / "infra/terraform/modules/storage/postgresql"
    postgres_dir.mkdir(parents=True)
    rds_dir = tmp_path / "infra/terraform/modules/storage/rds-postgresql"
    rds_dir.mkdir(parents=True)
    postgres_ref = "postgres:16-alpine@sha256:" + "a" * 64
    stale_ref = "postgres:16-alpine@sha256:" + "b" * 64
    (postgres_dir / "main.tf").write_text(
        f'image = "{postgres_ref}"\nimage = "{stale_ref}"\n'
    )
    (rds_dir / "main.tf").write_text(f'image = "{postgres_ref}"\n')

    catalog = {"components": {"images": {"postgres": postgres_ref}}}
    result = release._check_images_match_deployment_sources(tmp_path, catalog)

    assert not result.ok
    assert "postgres" in result.detail
    assert stale_ref in result.detail


def test_check_images_match_deployment_sources_flags_bootstrap_image_drift(tmp_path: Path) -> None:
    polaris_dir = tmp_path / "infra/terraform/modules/catalog/polaris"
    polaris_dir.mkdir(parents=True)
    openmetadata_dir = tmp_path / "infra/terraform/modules/governance/openmetadata"
    openmetadata_dir.mkdir(parents=True)
    bootstrap_ref = "alpine/k8s:1.30.0@sha256:" + "a" * 64
    stale_ref = "alpine/k8s:1.30.0@sha256:" + "b" * 64
    (polaris_dir / "variables.tf").write_text(f'default = "{bootstrap_ref}"\n')
    (openmetadata_dir / "variables.tf").write_text(f'default = "{stale_ref}"\n')

    catalog = {"components": {"images": {"k8s_bootstrap": bootstrap_ref}}}
    result = release._check_images_match_deployment_sources(tmp_path, catalog)

    assert not result.ok
    assert "k8s_bootstrap" in result.detail
    assert stale_ref in result.detail


def test_check_images_match_deployment_sources_flags_chart_managed_image_drift(
    tmp_path: Path,
) -> None:
    helm_values = tmp_path / "infra/helm/values/local"
    helm_values.mkdir(parents=True)
    opensearch_ref = "opensearchproject/opensearch:3.3.2@sha256:" + "a" * 64
    stale_redis_ref = "docker.io/bitnamilegacy/redis:7.0.10@sha256:" + "b" * 64
    (helm_values / "openmetadata-deps.yaml").write_text(
        "opensearch:\n  image:\n    repository: opensearchproject/opensearch\n"
        f'    tag: "3.3.2@sha256:{"a" * 64}"\n'
    )
    (helm_values / "superset.yaml").write_text(
        "redis:\n  image:\n    registry: docker.io\n    repository: bitnamilegacy/redis\n"
        f'    tag: "7.0.10@sha256:{"b" * 64}"\n'
        "initImage:\n  repository: apache/superset\n"
        f'  tag: "dockerize@sha256:{"c" * 64}"\n'
    )

    catalog = {
        "components": {
            "images": {
                "opensearch": opensearch_ref,
                "superset_redis": "docker.io/bitnamilegacy/redis:7.0.10@sha256:" + "d" * 64,
                "superset_init": "apache/superset:dockerize@sha256:" + "c" * 64,
            }
        }
    }
    result = release._check_images_match_deployment_sources(tmp_path, catalog)

    assert not result.ok
    assert "superset_redis" in result.detail
    assert stale_redis_ref in result.detail


def test_check_images_match_deployment_sources_flags_aws_postgres_reference_drift(
    tmp_path: Path,
) -> None:
    postgres_dir = tmp_path / "infra/terraform/modules/storage/postgresql"
    postgres_dir.mkdir(parents=True)
    rds_dir = tmp_path / "infra/terraform/modules/storage/rds-postgresql"
    rds_dir.mkdir(parents=True)
    postgres_ref = "postgres:16-alpine@sha256:" + "a" * 64
    stale_ref = "postgres:16-alpine@sha256:" + "b" * 64
    (postgres_dir / "main.tf").write_text(f'image = "{postgres_ref}"\n')
    (rds_dir / "main.tf").write_text(f'image = "{stale_ref}"\n')

    catalog = {"components": {"images": {"postgres": postgres_ref}}}
    result = release._check_images_match_deployment_sources(tmp_path, catalog)

    assert not result.ok
    assert "rds-postgresql/main.tf" in result.detail
    assert stale_ref in result.detail


def test_check_images_match_deployment_sources_requires_every_registered_image_in_catalog(
    tmp_path: Path,
) -> None:
    result = release._check_images_match_deployment_sources(tmp_path, {"components": {"images": {}}})

    assert not result.ok
    assert "trino: registered deployment image is missing from the component catalog" in result.detail


def test_check_dockerfiles_pinned_passes_on_real_repo() -> None:
    result = release._check_dockerfiles_pinned(ROOT)
    assert result.ok, result.detail


def test_check_dockerfiles_pinned_flags_unpinned_from(tmp_path: Path) -> None:
    images_dir = tmp_path / "images" / "demo"
    images_dir.mkdir(parents=True)
    (images_dir / "Dockerfile").write_text("FROM python:3.12-slim\n")
    result = release._check_dockerfiles_pinned(tmp_path)
    assert not result.ok
    assert "Dockerfile" in result.detail


def test_check_actions_sha_pinned_flags_unpinned_action(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "demo.yml").write_text("jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n")
    result = release._check_actions_sha_pinned(tmp_path, {"components": {"actions": {}}})
    assert not result.ok
    assert "actions/checkout@v4" in result.detail


def test_check_actions_sha_pinned_flags_uncataloged_pinned_action(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    sha = "a" * 40
    (workflows / "demo.yml").write_text(f"jobs:\n  a:\n    steps:\n      - uses: actions/checkout@{sha}\n")
    result = release._check_actions_sha_pinned(tmp_path, {"components": {"actions": {}}})
    assert not result.ok
    assert "checkout" in result.detail


def test_check_actions_flags_a_mismatch_masked_by_a_later_cataloged_occurrence(tmp_path: Path) -> None:
    """A stale ref in one workflow must not be hidden by a cataloged ref in another.

    Collapsing occurrences into a dict keyed by action name let the later file win:
    'checks.yml' sorts before 'release.yml', so release.yml's cataloged SHA
    overwrote checks.yml's stale one and the gate passed.
    """
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    stale, cataloged = "b" * 40, "c" * 40
    (workflows / "checks.yml").write_text(
        f"jobs:\n  a:\n    steps:\n      - uses: actions/checkout@{stale}\n"
    )
    (workflows / "release.yml").write_text(
        f"jobs:\n  a:\n    steps:\n      - uses: actions/checkout@{cataloged}\n"
    )

    result = release._check_actions_sha_pinned(
        tmp_path, {"components": {"actions": {"actions/checkout": cataloged}}}
    )

    assert not result.ok
    assert "checks.yml" in result.detail
    assert stale in result.detail


def test_check_actions_passes_when_every_occurrence_matches_the_catalog(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    sha = "d" * 40
    for name in ("checks.yml", "release.yml"):
        (workflows / name).write_text(f"jobs:\n  a:\n    steps:\n      - uses: actions/checkout@{sha}\n")

    result = release._check_actions_sha_pinned(
        tmp_path, {"components": {"actions": {"actions/checkout": sha}}}
    )

    assert result.ok
    assert "2 action reference(s)" in result.detail


def test_check_actions_flags_unused_catalog_entry(tmp_path: Path) -> None:
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    sha = "d" * 40
    (workflows / "release.yml").write_text(
        f"jobs:\n  a:\n    steps:\n      - uses: actions/checkout@{sha}\n"
    )

    result = release._check_actions_sha_pinned(
        tmp_path,
        {
            "components": {
                "actions": {
                    "actions/checkout": sha,
                    "actions/cache": "e" * 40,
                }
            }
        },
    )

    assert not result.ok
    assert "unused entries" in result.detail
    assert "actions/cache" in result.detail


def test_write_checksums_excludes_its_own_custom_output_path(tmp_path: Path) -> None:
    """A custom --output inside the checksummed directory must never hash itself,
    even across a rerun where the file already exists with stale content.
    """
    (tmp_path / "asset.bin").write_text("v1")
    out = tmp_path / "custom-checksums.txt"
    release.write_checksums(tmp_path, output=out)

    (tmp_path / "asset.bin").write_text("v2-different-content")
    release.write_checksums(tmp_path, output=out)

    content = out.read_text()
    assert "custom-checksums.txt" not in content
    assert "asset.bin" in content


def test_compatibility_matrix_doc_is_up_to_date_on_real_repo() -> None:
    catalog = release.load_catalog(ROOT / "release/component-catalog.yaml")
    result = release._check_compatibility_matrix_up_to_date(ROOT, catalog)
    assert result.ok, result.detail


def test_compatibility_matrix_doc_flags_drift(tmp_path: Path) -> None:
    catalog = release.load_catalog(_write_catalog(tmp_path))
    docs_dir = tmp_path / "docs" / "release"
    docs_dir.mkdir(parents=True)
    (docs_dir / "compatibility-matrix.md").write_text("stale content from before the last catalog bump\n")

    result = release._check_compatibility_matrix_up_to_date(tmp_path, catalog)

    assert not result.ok
    assert "does not match a fresh render" in result.detail


def test_compatibility_matrix_doc_missing_is_flagged(tmp_path: Path) -> None:
    catalog = release.load_catalog(_write_catalog(tmp_path))
    result = release._check_compatibility_matrix_up_to_date(tmp_path, catalog)
    assert not result.ok
    assert "does not exist" in result.detail
