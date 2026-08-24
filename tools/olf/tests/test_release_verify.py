from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import typer

from olf.commands import release


class _Runner:
    def run(self, *args, **kwargs):  # noqa: ANN002,ANN003
        return None


class _Resolver:
    def resolve(self, name: str) -> Path:
        return Path(name)


class _Tools:
    runner = _Runner()
    resolver = _Resolver()


def test_release_identity_escapes_tag_and_repository() -> None:
    assert release._release_identity("malon64/openlakeforge", "v0.1.0-alpha.1") == (
        r"^https://github\.com/malon64/openlakeforge/\.github/workflows/release\.yml@refs/tags/v0\.1\.0\-alpha\.1$"
    )


def test_verify_release_assets_rejects_checksum_path_traversal(tmp_path: Path) -> None:
    (tmp_path / "checksums.txt").write_text("deadbeef  ../outside\n")
    (tmp_path / "checksums.txt.bundle").write_text("bundle")
    (tmp_path / "component-manifest.json").write_text("{}")

    with pytest.raises(typer.Exit):
        release._verify_release_assets(
            tmp_path,
            tag="v0.1.0-alpha.1",
            repo_slug="malon64/openlakeforge",
            tools=_Tools(),
        )


def test_verify_release_assets_checks_all_local_hashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = tmp_path / "asset.txt"
    payload.write_text("payload")
    (tmp_path / "checksums.txt").write_text(f"{hashlib.sha256(payload.read_bytes()).hexdigest()}  asset.txt\n")
    (tmp_path / "checksums.txt.bundle").write_text("bundle")
    (tmp_path / "component-manifest.json").write_text("{}")

    release._verify_release_assets(
        tmp_path,
        tag="v0.1.0-alpha.1",
        repo_slug="malon64/openlakeforge",
        tools=_Tools(),
    )


def test_write_local_sboms_uses_syft_before_checksums(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    class Runner:
        def run(self, argv, **kwargs):  # noqa: ANN001, ANN003
            calls.append(argv)

    class Resolver:
        def resolve(self, name: str) -> Path:
            assert name == "syft"
            return Path("syft")

    class Tools:
        runner = Runner()
        resolver = Resolver()

    release._write_local_sboms(tmp_path, images={"project-code": "project", "superset": "superset"}, tools=Tools())

    assert calls == [
        ["syft", "project", "-o", f"spdx-json={tmp_path / 'project-code.spdx.json'}"],
        ["syft", "superset", "-o", f"spdx-json={tmp_path / 'superset.spdx.json'}"],
    ]
