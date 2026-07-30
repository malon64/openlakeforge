from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from olf import revision


class FakeS3Client:
    """A minimal in-memory stand-in for the boto3 S3 client surface revision.py uses."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict[str, str]] = {}

    def put_object(self, *, Bucket: str, Key: str, Body, ContentType: str = "", Metadata=None) -> None:  # noqa: N803
        data = Body.read() if hasattr(Body, "read") else Body
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.objects[Key] = data
        self.metadata[Key] = dict(Metadata or {})

    def get_object(self, *, Bucket: str, Key: str) -> dict:  # noqa: N803
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": "not found"}}, "GetObject")
        return {"Body": _FakeBody(self.objects[Key]), "Metadata": dict(self.metadata.get(Key, {}))}

    def get_paginator(self, operation_name: str) -> _FakePaginator:
        assert operation_name == "list_objects_v2"
        return _FakePaginator(self)


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakePaginator:
    def __init__(self, client: FakeS3Client) -> None:
        self._client = client

    def paginate(self, *, Bucket: str, Prefix: str):  # noqa: N803
        contents = [{"Key": key} for key in sorted(self._client.objects) if key.startswith(Prefix)]
        yield {"Contents": contents}


def _write_runtime_root(root: Path, *, entities: dict[str, bytes]) -> None:
    for relative, content in entities.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


@pytest.fixture
def runtime_root(tmp_path: Path) -> Path:
    root = tmp_path / "runtime"
    _write_runtime_root(
        root,
        entities={
            "configs/sales/order_revenue/order_revenue.yml": b"config-a",
            "profiles/sales/order_revenue/local-k8s.yml": b"profile-a",
            "manifests/sales/order_revenue/order_revenue.manifest.json": b"manifest-a",
        },
    )
    return root


def test_compute_revision_is_deterministic_and_path_order_independent(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    entities = {
        "configs/sales/order_revenue/order_revenue.yml": b"config",
        "manifests/sales/order_revenue/order_revenue.manifest.json": b"manifest",
        "profiles/sales/order_revenue/local-k8s.yml": b"profile",
    }
    _write_runtime_root(root_a, entities=entities)
    # Same content, different on-disk creation order.
    _write_runtime_root(
        root_b,
        entities={
            "profiles/sales/order_revenue/local-k8s.yml": b"profile",
            "manifests/sales/order_revenue/order_revenue.manifest.json": b"manifest",
            "configs/sales/order_revenue/order_revenue.yml": b"config",
        },
    )
    assert revision.compute_revision(root_a) == revision.compute_revision(root_b)
    assert revision.compute_revision(root_a).startswith("sha256:")


def test_compute_revision_changes_when_content_changes(runtime_root: Path) -> None:
    before = revision.compute_revision(runtime_root)
    manifest_path = runtime_root / "manifests/sales/order_revenue/order_revenue.manifest.json"
    manifest_path.write_bytes(b"manifest-b")
    after = revision.compute_revision(runtime_root)
    assert before != after


def test_diff_revisions_reports_missing_extra_and_changed_keys() -> None:
    expected = revision.manifest_from_entries(
        {
            "floe/manifests/sales/order_revenue/order_revenue.manifest.json": "a" * 64,
            "floe/manifests/sales/customer_health/customer_health.manifest.json": "b" * 64,
        }
    )
    actual = revision.manifest_from_entries(
        {
            "floe/manifests/sales/order_revenue/order_revenue.manifest.json": "c" * 64,
            "floe/manifests/supply_chain/inventory_reliability/inventory_reliability.manifest.json": "d" * 64,
        }
    )
    diff = revision.diff_revisions(expected, actual)
    assert not diff.matches
    assert diff.changed_keys == ("floe/manifests/sales/order_revenue/order_revenue.manifest.json",)
    assert diff.missing_keys == ("floe/manifests/sales/customer_health/customer_health.manifest.json",)
    assert diff.extra_keys == (
        "floe/manifests/supply_chain/inventory_reliability/inventory_reliability.manifest.json",
    )


def test_publish_and_fetch_sidecar_round_trip(runtime_root: Path) -> None:
    client = FakeS3Client()
    manifest = revision.compute_revision_manifest(runtime_root)
    revision.publish_sidecar(client, "ops-bucket", manifest)

    fetched = revision.fetch_sidecar(client, "ops-bucket")
    assert fetched.revision == manifest.revision
    assert fetched.entries == manifest.entries
    assert client.metadata[revision.REVISION_SIDECAR_KEY][revision.REVISION_METADATA_KEY] == manifest.revision


def test_fetch_sidecar_missing_raises_revision_error() -> None:
    client = FakeS3Client()
    with pytest.raises(revision.RevisionError, match="no floe/manifests/REVISION sidecar"):
        revision.fetch_sidecar(client, "ops-bucket")


def test_verify_fails_and_names_diverging_key_on_interrupted_deploy(runtime_root: Path) -> None:
    """Simulates an interrupted deploy: the project-code image was built at
    revision A (computed from a fresh manifest set) but the ops bucket still
    holds the previous deploy's revision B artifacts, because the upload step
    that would have replaced them and republished the sidecar never ran.
    `olf revision verify --expected <revision-A>` must fail hard and name the
    specific artifact key whose content still reflects revision B.
    """
    from olf import s3

    client = FakeS3Client()

    # Revision B: the previous, successfully published deploy. A real deploy
    # stamps every uploaded object's metadata with the revision it belongs to
    # (see `olf.s3._put_objects`), so replicate that here.
    old_uploads = {
        "floe/configs/sales/order_revenue/order_revenue.yml": b"config-REVISION-B",
        "floe/profiles/sales/order_revenue/local-k8s.yml": b"profile-REVISION-B",
        "floe/manifests/sales/order_revenue/order_revenue.manifest.json": b"order-revenue-manifest-REVISION-B",
    }
    old_revision = revision.manifest_from_entries(
        {key: hashlib.sha256(content).hexdigest() for key, content in old_uploads.items()}
    ).revision
    for key, content in old_uploads.items():
        client.put_object(
            Bucket="ops-bucket",
            Key=key,
            Body=content,
            ContentType="application/json",
            Metadata={revision.REVISION_METADATA_KEY: old_revision},
        )
    revision.publish_sidecar(client, "ops-bucket", revision.fetch_bucket_manifest(client, "ops-bucket"))

    # Revision A: computed from a freshly generated (but never-uploaded)
    # runtime root, and baked into the new project-code image build. The
    # deploy is killed after the image build but before `artifacts
    # upload-manifests` runs, so the bucket never advances past revision B.
    _write_runtime_root(
        runtime_root,
        entities={
            "configs/sales/order_revenue/order_revenue.yml": b"config-REVISION-B",
            "profiles/sales/order_revenue/local-k8s.yml": b"profile-REVISION-B",
            # Only the manifest content changed in the new deploy.
            "manifests/sales/order_revenue/order_revenue.manifest.json": b"order-revenue-manifest-REVISION-A",
        },
    )
    new_revision = revision.compute_revision(runtime_root)
    assert new_revision != old_revision

    diff = revision.verify(client, "ops-bucket", new_revision)

    assert not diff.matches
    assert diff.expected_revision == new_revision
    assert diff.actual_revision == old_revision
    # The bucket was never re-uploaded, so every object still carries the
    # stale revision-B metadata tag; the diagnosable diff must name each one,
    # including the manifest whose content is the actual divergent artifact.
    assert set(diff.changed_keys) == set(old_uploads)
    assert "floe/manifests/sales/order_revenue/order_revenue.manifest.json" in diff.describe()

    uploads = s3.discover_runtime_artifacts(runtime_root)
    assert revision.manifest_from_uploads(uploads).revision == new_revision


def test_verify_passes_when_bucket_matches_expected(runtime_root: Path) -> None:
    from olf import s3

    client = FakeS3Client()
    uploads = s3.discover_runtime_artifacts(runtime_root)
    manifest = revision.manifest_from_uploads(uploads)
    for upload in uploads:
        client.put_object(
            Bucket="ops-bucket", Key=upload.key, Body=upload.path.read_bytes(), ContentType="application/json"
        )
    revision.publish_sidecar(client, "ops-bucket", manifest)

    diff = revision.verify(client, "ops-bucket", manifest.revision)
    assert diff.matches
