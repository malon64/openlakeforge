from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from olf.toolchain.manager import ToolchainManager
from olf.toolchain.platform import Platform
from olf.toolchain.spec import ToolchainCatalogError, load_specs

_PLATFORM = Platform(os="linux", arch="amd64")
_TOOLS = ("terraform", "helm", "kubectl", "kind")


def _archive_bytes(tool: str, spec) -> bytes:  # noqa: ANN001
    """Build a real zip/tar.gz/raw payload matching `spec`'s own archive
    layout, so the fake downloader exercises the same extraction code path
    a real download would.
    """
    # Version is embedded in the payload, matching a real release: a
    # version bump always changes the binary's bytes/digest too, so a fake
    # digest collision across versions (which would mask a version-bump
    # re-provisioning bug behind cache reuse) can't happen here.
    payload = f"pretend {tool} binary v{spec.version}".encode()
    if spec.archive == "raw":
        return payload
    if spec.archive == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr(spec.member, payload)
        return buffer.getvalue()
    if spec.archive == "tar.gz":
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
            info = tarfile.TarInfo(name=spec.member)
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
        return buffer.getvalue()
    raise AssertionError(spec.archive)


def _catalog_and_digests(version: str = "1.0.0") -> tuple[dict, dict[str, str]]:
    """A catalog with placeholder digests, plus the real digests for the
    fake archives that will actually be served - computed by round-tripping
    through the real `load_specs` so archive kind/member always matches
    what `ToolchainManager` will really request.
    """
    placeholder = "sha256:" + "0" * 64
    draft_catalog = {
        "components": {
            "toolchain": {tool: {"version": version, "platforms": {_PLATFORM.key: placeholder}} for tool in _TOOLS}
        }
    }
    specs = load_specs(draft_catalog, platform=_PLATFORM)
    digests = {tool: "sha256:" + hashlib.sha256(_archive_bytes(tool, spec)).hexdigest() for tool, spec in specs.items()}
    catalog = {
        "distribution": {"version": "0.1.0-alpha.1"},
        "components": {
            "toolchain": {
                tool: {"version": version, "platforms": {_PLATFORM.key: digests[tool]}} for tool in _TOOLS
            }
        },
    }
    return catalog, digests


class _FakeDownloader:
    def __init__(self, specs: dict) -> None:
        self._specs = specs
        self.calls: list[str] = []

    def fetch(self, url: str, *, destination: Path) -> None:
        self.calls.append(url)
        tool = next(name for name, spec in self._specs.items() if spec.url == url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(_archive_bytes(tool, self._specs[tool]))


def _manager(
    tmp_path: Path, *, catalog: dict | None = None, home: Path | None = None
) -> tuple[ToolchainManager, _FakeDownloader]:
    resolved_catalog = catalog or _catalog_and_digests()[0]
    specs = load_specs(resolved_catalog, platform=_PLATFORM)
    downloader = _FakeDownloader(specs)
    manager = ToolchainManager(
        home=home or (tmp_path / "home"),
        distribution_version=str(resolved_catalog["distribution"]["version"]),
        platform=_PLATFORM,
        specs=specs,
        downloader=downloader,
    )
    return manager, downloader


def test_resolve_provisions_a_managed_tool_on_first_use(tmp_path: Path) -> None:
    manager, downloader = _manager(tmp_path)

    path = manager.resolve("kind")

    assert path.is_file()
    assert path.read_bytes() == b"pretend kind binary v1.0.0"
    assert len(downloader.calls) == 1


def test_resolve_is_idempotent_and_touches_the_network_only_once(tmp_path: Path) -> None:
    manager, downloader = _manager(tmp_path)

    first = manager.resolve("terraform")
    second = manager.resolve("terraform")

    assert first == second
    assert first.read_bytes() == b"pretend terraform binary v1.0.0"
    assert len(downloader.calls) == 1


def test_resolve_writes_a_receipt_recording_version_and_digest(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)

    manager.resolve("helm")

    receipt = json.loads(manager.receipt_path.read_text())
    assert receipt["helm"]["version"] == manager.specs["helm"].version
    assert receipt["helm"]["sha256"] == manager.specs["helm"].sha256


def test_resolve_reprovisions_when_the_catalog_bumps_the_version(tmp_path: Path) -> None:
    home = tmp_path / "home"
    manager_v1, _ = _manager(tmp_path, home=home)
    manager_v1.resolve("kubectl")

    catalog_v2, _ = _catalog_and_digests(version="2.0.0")
    manager_v2, downloader_v2 = _manager(tmp_path, catalog=catalog_v2, home=home)
    # Same distribution version (receipts are per version_dir) but a bumped
    # tool version - the receipt no longer matches the catalog's spec.
    manager_v2 = ToolchainManager(
        home=home,
        distribution_version=manager_v1.distribution_version,
        platform=_PLATFORM,
        specs=manager_v2.specs,
        downloader=downloader_v2,
    )

    manager_v2.resolve("kubectl")

    assert len(downloader_v2.calls) == 1  # version differs from the receipt, so it re-provisions


def test_resolve_raises_for_an_unmanaged_tool_name(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    with pytest.raises(KeyError):
        manager.resolve("docker")


def test_ensure_all_provisions_every_managed_tool(tmp_path: Path) -> None:
    manager, downloader = _manager(tmp_path)

    paths = manager.ensure_all()

    assert set(paths) == set(_TOOLS)
    assert len(downloader.calls) == len(_TOOLS)
    assert all(path.is_file() for path in paths.values())


def test_two_distribution_versions_keep_independent_toolchains(tmp_path: Path) -> None:
    home = tmp_path / "home"
    catalog_a, _ = _catalog_and_digests()
    catalog_a["distribution"]["version"] = "0.1.0-alpha.1"
    catalog_b, _ = _catalog_and_digests()
    catalog_b["distribution"]["version"] = "0.2.0-alpha.1"

    manager_a, _ = _manager(tmp_path, catalog=catalog_a, home=home)
    manager_b, _ = _manager(tmp_path, catalog=catalog_b, home=home)

    path_a = manager_a.resolve("kind")
    path_b = manager_b.resolve("kind")

    assert path_a != path_b
    assert path_a.is_file()
    assert path_b.is_file()
    assert manager_a.version_dir != manager_b.version_dir


def test_from_catalog_path_raises_a_typed_error_for_a_missing_catalog(tmp_path: Path) -> None:
    with pytest.raises(ToolchainCatalogError):
        ToolchainManager.from_catalog_path(tmp_path / "missing.yaml", home=tmp_path / "home", platform=_PLATFORM)


def test_prune_requires_exactly_one_selector(tmp_path: Path) -> None:
    manager, _ = _manager(tmp_path)
    with pytest.raises(ValueError):
        manager.prune()
    with pytest.raises(ValueError):
        manager.prune(version="1.0.0", remove_all=True)


def test_prune_all_removes_every_installed_version(tmp_path: Path) -> None:
    home = tmp_path / "home"
    catalog_a, _ = _catalog_and_digests()
    catalog_a["distribution"]["version"] = "0.1.0-alpha.1"
    catalog_b, _ = _catalog_and_digests()
    catalog_b["distribution"]["version"] = "0.2.0-alpha.1"
    manager_a, _ = _manager(tmp_path, catalog=catalog_a, home=home)
    manager_b, _ = _manager(tmp_path, catalog=catalog_b, home=home)
    manager_a.resolve("kind")
    manager_b.resolve("kind")

    removed = manager_a.prune(remove_all=True)

    assert sorted(p.name for p in removed) == ["0.1.0-alpha.1", "0.2.0-alpha.1"]
    assert not manager_a.version_dir.exists()
    assert not manager_b.version_dir.exists()


def test_prune_keep_current_preserves_only_the_active_distribution(tmp_path: Path) -> None:
    home = tmp_path / "home"
    catalog_a, _ = _catalog_and_digests()
    catalog_a["distribution"]["version"] = "0.1.0-alpha.1"
    catalog_b, _ = _catalog_and_digests()
    catalog_b["distribution"]["version"] = "0.2.0-alpha.1"
    manager_a, _ = _manager(tmp_path, catalog=catalog_a, home=home)
    manager_b, _ = _manager(tmp_path, catalog=catalog_b, home=home)
    manager_a.resolve("kind")
    manager_b.resolve("kind")

    removed = manager_b.prune(keep_current=True)

    assert [p.name for p in removed] == ["0.1.0-alpha.1"]
    assert not manager_a.version_dir.exists()
    assert manager_b.version_dir.exists()


def test_prune_never_removes_anything_outside_olf_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    outside = tmp_path / "definitely-not-olf-home"
    outside.mkdir()
    (outside / "marker.txt").write_text("do not touch")
    (home / "toolchains").mkdir(parents=True)
    escape_link = home / "toolchains" / "escaped-version"
    escape_link.symlink_to(outside)

    manager, _ = _manager(tmp_path, home=home)

    manager.prune(remove_all=True)

    assert outside.is_dir()
    assert (outside / "marker.txt").is_file()
