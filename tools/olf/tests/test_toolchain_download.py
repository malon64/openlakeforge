from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from olf.toolchain.download import cache_path, fetch_verified
from olf.toolchain.errors import ToolchainVerificationError
from olf.toolchain.platform import Platform
from olf.toolchain.spec import ToolSpec

_CONTENT = b"fake-tool-binary-content"
_DIGEST = hashlib.sha256(_CONTENT).hexdigest()


def _spec(sha256: str = f"sha256:{_DIGEST}") -> ToolSpec:
    return ToolSpec(
        name="kind",
        version="1.0.0",
        platform=Platform(os="linux", arch="amd64"),
        sha256=sha256,
        url="https://example.invalid/kind",
        archive="raw",
    )


class _FakeDownloader:
    def __init__(self, content: bytes = _CONTENT) -> None:
        self.content = content
        self.calls: list[str] = []

    def fetch(self, url: str, *, destination: Path) -> None:
        self.calls.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self.content)


class _FailingDownloader:
    def fetch(self, url: str, *, destination: Path) -> None:
        raise RuntimeError("network is unreachable")


def test_fetch_verified_downloads_and_verifies(tmp_path: Path) -> None:
    downloader = _FakeDownloader()
    spec = _spec()

    result = fetch_verified(downloader, spec, cache_root=tmp_path)

    assert downloader.calls == [spec.url]
    assert result == cache_path(tmp_path, spec)
    assert result.read_bytes() == _CONTENT


def test_fetch_verified_reuses_a_valid_cache_entry_without_downloading(tmp_path: Path) -> None:
    downloader = _FakeDownloader()
    spec = _spec()
    fetch_verified(downloader, spec, cache_root=tmp_path)

    fetch_verified(downloader, spec, cache_root=tmp_path)

    assert downloader.calls == [spec.url]  # only the first call touched the network


def test_fetch_verified_rejects_a_digest_mismatch_and_leaves_no_file(tmp_path: Path) -> None:
    downloader = _FakeDownloader(content=b"wrong content entirely")
    spec = _spec()

    with pytest.raises(ToolchainVerificationError):
        fetch_verified(downloader, spec, cache_root=tmp_path)

    assert not cache_path(tmp_path, spec).exists()


def test_fetch_verified_redownloads_a_stale_cache_entry(tmp_path: Path) -> None:
    spec = _spec()
    destination = cache_path(tmp_path, spec)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"corrupted leftover from a prior run")
    downloader = _FakeDownloader()

    result = fetch_verified(downloader, spec, cache_root=tmp_path)

    assert downloader.calls == [spec.url]
    assert result.read_bytes() == _CONTENT


def test_fetch_verified_propagates_download_failures(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="unreachable"):
        fetch_verified(_FailingDownloader(), _spec(), cache_root=tmp_path)
