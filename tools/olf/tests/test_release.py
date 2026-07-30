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
