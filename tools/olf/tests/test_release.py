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
            "helm": {"trino": "1.42.2"},
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
    rendered = release.render_compatibility_matrix(catalog)
    assert "hashicorp/aws" in rendered
    assert "5.100.0" in rendered
    assert "trino" in rendered
    assert "1.42.2" in rendered
    assert "python_project_code_base" not in rendered  # sanity: no stray key names leak in


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


def test_check_images_digest_pinned_flags_missing_digest() -> None:
    catalog = {"components": {"images": {"bad": "python:3.12-slim"}}}
    result = release._check_images_digest_pinned(catalog)
    assert not result.ok
    assert "bad" in result.detail


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


def test_check_lockfiles_flags_missing_lockfile(tmp_path: Path) -> None:
    catalog = {"components": {"python": {"project_code_lock": "does/not/exist.lock", "tooling_lock": "also/missing"}}}
    result = release._check_lockfiles(tmp_path, catalog)
    assert not result.ok


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


def test_write_checksums_excludes_its_own_custom_output_path(tmp_path: Path) -> None:
    """A custom --output inside the checksummed directory must never hash itself.

    `compute_checksums` only ever excluded the literal name "checksums.txt". On
    a rerun with a custom output path, the previous run's manifest file was
    still sitting in `directory`, got hashed like any other asset, and the new
    manifest overwrote it a moment later -- recording a checksum for bytes that
    no longer existed there. `sha256sum -c` fails on that entry forever after.
    """
    (tmp_path / "asset.bin").write_text("v1")
    out = tmp_path / "custom-checksums.txt"
    release.write_checksums(tmp_path, output=out)

    (tmp_path / "asset.bin").write_text("v2-different-content")
    release.write_checksums(tmp_path, output=out)

    content = out.read_text()
    assert "custom-checksums.txt" not in content
    assert "asset.bin" in content


def _write_pyproject(path: Path, dependencies: list[str]) -> None:
    deps = ",\n  ".join(f'"{dep}"' for dep in dependencies)
    path.write_text(f'[project]\nname = "demo"\ndependencies = [\n  {deps},\n]\n')


def _write_requirements_lock(path: Path, *, direct: dict[str, str], transitive: dict[str, str] | None = None) -> None:
    # Must match the owner_marker _check_lockfiles_synced_with_pyproject passes:
    # "openlakeforge-project-code" (the real repo's images/project-code package name).
    lines = []
    for name, version in direct.items():
        lines.append(f"{name}=={version}")
        lines.append("    # via openlakeforge-project-code (images/project-code/pyproject.toml)")
    for name, version in (transitive or {}).items():
        lines.append(f"{name}=={version}")
        lines.append("    # via some-other-package")
    path.write_text("\n".join(lines) + "\n")


def test_check_lockfiles_synced_passes_on_real_repo_catalog() -> None:
    catalog = release.load_catalog(ROOT / "release/component-catalog.yaml")
    result = release._check_lockfiles_synced_with_pyproject(ROOT, catalog)
    assert result.ok, result.detail


def test_check_lockfiles_synced_flags_a_pyproject_dependency_absent_from_the_lock(tmp_path: Path) -> None:
    """The scenario from the review: pyproject.toml gains a dependency and the
    lock is never regenerated. check-project-code.sh installs straight from
    pyproject metadata and would pass; only this check catches the drift before
    the built image installs the stale lock instead.
    """
    project_code = tmp_path / "images/project-code"
    project_code.mkdir(parents=True)
    _write_pyproject(project_code / "pyproject.toml", ["boto3==1.37.3", "httpx==0.27.0"])
    _write_requirements_lock(project_code / "requirements.lock", direct={"boto3": "1.37.3"})

    catalog = {"components": {"python": {"project_code_lock": "images/project-code/requirements.lock"}}}
    result = release._check_lockfiles_synced_with_pyproject(tmp_path, catalog)

    assert not result.ok
    assert "httpx" in result.detail
    assert "no direct entry" in result.detail


def test_check_lockfiles_synced_flags_a_version_pinned_differently_than_locked(tmp_path: Path) -> None:
    project_code = tmp_path / "images/project-code"
    project_code.mkdir(parents=True)
    _write_pyproject(project_code / "pyproject.toml", ["dagster==1.14.0"])
    _write_requirements_lock(project_code / "requirements.lock", direct={"dagster": "1.13.6"})

    catalog = {"components": {"python": {"project_code_lock": "images/project-code/requirements.lock"}}}
    result = release._check_lockfiles_synced_with_pyproject(tmp_path, catalog)

    assert not result.ok
    assert "dagster" in result.detail
    assert "1.14.0" in result.detail
    assert "1.13.6" in result.detail


def test_check_lockfiles_synced_ignores_transitive_only_and_range_pinned_deps(tmp_path: Path) -> None:
    """A package present only as a transitive dependency must not satisfy a
    direct pyproject requirement, and a range-pinned dependency (no exact '==')
    is checked for presence only, since verifying a range needs a resolver.
    """
    project_code = tmp_path / "images/project-code"
    project_code.mkdir(parents=True)
    _write_pyproject(project_code / "pyproject.toml", ["boto3==1.37.3", "kubernetes<36"])
    _write_requirements_lock(
        project_code / "requirements.lock",
        direct={"boto3": "1.37.3", "kubernetes": "35.0.0"},
        transitive={"botocore": "1.37.3"},
    )

    catalog = {"components": {"python": {"project_code_lock": "images/project-code/requirements.lock"}}}
    result = release._check_lockfiles_synced_with_pyproject(tmp_path, catalog)
    assert result.ok, result.detail


def test_check_lockfiles_synced_flags_a_pyproject_dependency_absent_from_uv_lock(tmp_path: Path) -> None:
    tooling = tmp_path / "tools/olf"
    tooling.mkdir(parents=True)
    _write_pyproject(tooling / "pyproject.toml", ["boto3>=1.34,<2", "httpx>=0.27,<1"])
    (tooling / "uv.lock").write_text(
        """
[[package]]
name = "openlakeforge-tools"
version = "0.1.0"

[package.metadata]
requires-dist = [
    { name = "boto3", specifier = ">=1.34,<2" },
]
"""
    )

    catalog = {"components": {"python": {"tooling_lock": "tools/olf/uv.lock"}}}
    result = release._check_lockfiles_synced_with_pyproject(tmp_path, catalog)

    assert not result.ok
    assert "httpx" in result.detail
    assert "requires-dist" in result.detail
