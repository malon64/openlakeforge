import io
import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from olf import revision, s3


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.puts: list[tuple[str, str]] = []

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, io.BytesIO]:
        try:
            value = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject") from exc
        return {"Body": io.BytesIO(value)}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self.objects[(Bucket, Key)] = Body
        self.puts.append((Bucket, Key))


def _uploads(tmp_path: Path) -> list[s3.ManifestUpload]:
    config = tmp_path / "configs/sales/order_revenue/order_revenue.yml"
    profile = tmp_path / "profiles/sales/order_revenue/local-k8s.yml"
    manifest = tmp_path / "manifests/sales/order_revenue/order_revenue.manifest.json"
    for path, content in [(config, "config"), (profile, "profile"), (manifest, "{}")]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return s3.discover_runtime_artifacts(tmp_path)


def test_revision_is_stable_across_upload_order(tmp_path: Path) -> None:
    uploads = _uploads(tmp_path)
    original = revision.manifest_from_uploads(uploads).revision
    reversed_order = revision.manifest_from_uploads(list(reversed(uploads))).revision
    assert original == reversed_order


def test_publish_uses_revision_qualified_keys_and_is_idempotent(tmp_path: Path) -> None:
    uploads = _uploads(tmp_path)
    client = FakeS3()

    manifest = revision.publish(client, "ops", uploads)
    expected_prefix = f"floe/revisions/sha256/{manifest.revision.removeprefix('sha256:')}"
    assert {key for bucket, key in client.objects if bucket == "ops"} == {
        f"{expected_prefix}/{upload.key}" for upload in uploads
    } | {f"{expected_prefix}/REVISION.json"}

    first_put_count = len(client.puts)
    assert revision.publish(client, "ops", uploads) == manifest
    assert len(client.puts) == first_put_count


def test_publish_refuses_different_existing_content(tmp_path: Path) -> None:
    uploads = _uploads(tmp_path)
    client = FakeS3()
    manifest = revision.manifest_from_uploads(uploads)
    key = revision.revision_key(manifest.revision, uploads[0].key)
    client.objects[("ops", key)] = b"tampered"

    with pytest.raises(revision.RevisionError, match="collision"):
        revision.publish(client, "ops", uploads)


def test_publish_uses_one_byte_snapshot_per_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    uploads = _uploads(tmp_path)
    client = FakeS3()
    target = uploads[0].path
    original_read_bytes = Path.read_bytes
    reads = 0

    def changing_read_bytes(path: Path) -> bytes:
        nonlocal reads
        if path == target:
            reads += 1
            return b"before" if reads == 1 else b"after"
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", changing_read_bytes)
    manifest = revision.publish(client, "ops", uploads)

    assert reads == 1
    assert client.objects[("ops", revision.revision_key(manifest.revision, uploads[0].key))] == b"before"
    assert revision.verify(client, "ops", manifest.revision) == manifest


def test_verify_detects_tampered_artifact(tmp_path: Path) -> None:
    uploads = _uploads(tmp_path)
    client = FakeS3()
    manifest = revision.publish(client, "ops", uploads)
    key = revision.revision_key(manifest.revision, uploads[0].key)
    client.objects[("ops", key)] = b"tampered"

    with pytest.raises(revision.RevisionError, match="hashes to"):
        revision.verify(client, "ops", manifest.revision)


def test_verify_rejects_sidecar_with_inconsistent_aggregate(tmp_path: Path) -> None:
    uploads = _uploads(tmp_path)
    client = FakeS3()
    manifest = revision.publish(client, "ops", uploads)
    sidecar = revision.sidecar_key(manifest.revision)
    payload = json.loads(client.objects[("ops", sidecar)])
    payload["entries"][uploads[0].key] = "0" * 64
    client.objects[("ops", sidecar)] = json.dumps(payload).encode()

    with pytest.raises(revision.RevisionError, match="aggregate"):
        revision.verify(client, "ops", manifest.revision)
