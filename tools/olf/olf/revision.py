"""Immutable Floe runtime-artifact revision publishing.

A revision is a deterministic hash of the rendered Floe config, profile, and
manifest set. Revisions are written beneath an immutable, revision-qualified
prefix and are not an activation mechanism: consumers continue using their
current paths until the runtime-activation change selects a revision.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from olf import s3

REVISION_PREFIX = "floe/revisions"
SIDECAR_NAME = "REVISION.json"
_REVISION_PATTERN = re.compile(r"^sha256:([0-9a-f]{64})$")


class RevisionError(RuntimeError):
    """A rendered artifact set cannot be published or verified safely."""


@dataclass(frozen=True)
class _ArtifactSnapshot:
    upload: s3.ManifestUpload
    content: bytes


@dataclass(frozen=True)
class RevisionManifest:
    """Aggregate revision and the content hash of each legacy artifact key."""

    revision: str
    entries: dict[str, str]

    def to_json(self) -> str:
        return json.dumps(
            {"revision": self.revision, "entries": dict(sorted(self.entries.items()))},
            indent=2,
            sort_keys=True,
        ) + "\n"

    @classmethod
    def from_json(cls, value: str) -> RevisionManifest:
        try:
            payload = json.loads(value)
            revision = payload["revision"]
            entries = payload["entries"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RevisionError(f"malformed {SIDECAR_NAME}: {exc}") from exc
        if not isinstance(revision, str) or not isinstance(entries, dict) or not all(
            isinstance(key, str) and isinstance(digest, str) for key, digest in entries.items()
        ):
            raise RevisionError(f"malformed {SIDECAR_NAME}: revision and entries must be strings.")
        manifest = cls(revision=revision, entries=dict(entries))
        manifest.validate()
        return manifest

    def validate(self) -> None:
        _revision_digest(self.revision)
        actual = aggregate_revision(self.entries)
        if self.revision != actual:
            raise RevisionError(
                f"revision sidecar declares {self.revision}, but its entries aggregate to {actual}."
            )


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def aggregate_revision(entries: dict[str, str]) -> str:
    """Return a stable content address for a set of artifact-key digests."""
    if not entries:
        raise RevisionError("cannot compute a revision for an empty artifact set.")
    hasher = hashlib.sha256()
    for key in sorted(entries):
        hasher.update(f"{key}\0{entries[key]}\n".encode())
    return f"sha256:{hasher.hexdigest()}"


def manifest_from_uploads(uploads: list[s3.ManifestUpload]) -> RevisionManifest:
    return _manifest_from_snapshots(_snapshot_uploads(uploads))


def _snapshot_uploads(uploads: list[s3.ManifestUpload]) -> list[_ArtifactSnapshot]:
    return [_ArtifactSnapshot(upload=upload, content=upload.path.read_bytes()) for upload in uploads]


def _manifest_from_snapshots(snapshots: list[_ArtifactSnapshot]) -> RevisionManifest:
    entries = {snapshot.upload.key: _content_hash(snapshot.content) for snapshot in snapshots}
    if len(entries) != len(snapshots):
        raise RevisionError("runtime artifact discovery produced duplicate object keys.")
    return RevisionManifest(revision=aggregate_revision(entries), entries=entries)


def compute_revision(runtime_root: Path) -> RevisionManifest:
    uploads = s3.discover_runtime_artifacts(runtime_root)
    if not uploads:
        raise RevisionError(f"no rendered Floe runtime artifacts found under {runtime_root}.")
    return manifest_from_uploads(uploads)


def revision_prefix(revision: str) -> str:
    return f"{REVISION_PREFIX}/sha256/{_revision_digest(revision)}"


def revision_key(revision: str, artifact_key: str) -> str:
    return f"{revision_prefix(revision)}/{artifact_key}"


def sidecar_key(revision: str) -> str:
    return f"{revision_prefix(revision)}/{SIDECAR_NAME}"


def publish(client: Any, bucket: str, uploads: list[s3.ManifestUpload]) -> RevisionManifest:
    """Publish a content-addressed set without overwriting a different revision.

    A failed or partial publish leaves at most an unreferenced revision prefix;
    it cannot change the mutable runtime paths used by existing code servers.
    """
    snapshots = _snapshot_uploads(uploads)
    manifest = _manifest_from_snapshots(snapshots)
    for snapshot in snapshots:
        _publish_immutable(
            client,
            bucket,
            revision_key(manifest.revision, snapshot.upload.key),
            snapshot.content,
            content_type=_content_type(snapshot.upload.path),
        )
    _publish_immutable(
        client,
        bucket,
        sidecar_key(manifest.revision),
        manifest.to_json().encode("utf-8"),
        content_type="application/json",
    )
    return manifest


def verify(client: Any, bucket: str, revision: str) -> RevisionManifest:
    """Verify the immutable sidecar and every object it declares."""
    key = sidecar_key(revision)
    manifest = RevisionManifest.from_json(_read_object(client, bucket, key).decode("utf-8"))
    if manifest.revision != revision:
        raise RevisionError(f"sidecar at {key} declares {manifest.revision}, not requested {revision}.")
    for artifact_key, expected_digest in manifest.entries.items():
        actual_digest = _content_hash(_read_object(client, bucket, revision_key(revision, artifact_key)))
        if actual_digest != expected_digest:
            raise RevisionError(
                f"artifact {artifact_key} in revision {revision} hashes to {actual_digest}, "
                f"expected {expected_digest}."
            )
    return manifest


def _revision_digest(revision: str) -> str:
    match = _REVISION_PATTERN.fullmatch(revision)
    if not match:
        raise RevisionError(f"invalid revision {revision!r}; expected sha256:<64 lowercase hex characters>.")
    return match.group(1)


def _read_object(client: Any, bucket: str, key: str) -> bytes:
    content = _maybe_read_object(client, bucket, key)
    if content is None:
        raise RevisionError(f"missing immutable artifact s3://{bucket}/{key}.")
    return content


def _maybe_read_object(client: Any, bucket: str, key: str) -> bytes | None:
    try:
        response = client.get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = str((exc.response.get("Error") or {}).get("Code", ""))
        if code in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise RevisionError(f"failed to read immutable artifact s3://{bucket}/{key}.") from exc
    except BotoCoreError as exc:
        raise RevisionError(f"failed to read immutable artifact s3://{bucket}/{key}.") from exc
    content = response["Body"].read()
    return content if isinstance(content, bytes) else content.encode("utf-8")


def _publish_immutable(client: Any, bucket: str, key: str, content: bytes, *, content_type: str) -> None:
    existing = _maybe_read_object(client, bucket, key)
    if existing is None:
        try:
            client.put_object(Bucket=bucket, Key=key, Body=content, ContentType=content_type)
        except (BotoCoreError, ClientError) as exc:
            raise RevisionError(f"failed to publish immutable artifact s3://{bucket}/{key}.") from exc
        return
    if existing != content:
        raise RevisionError(
            f"immutable artifact collision at s3://{bucket}/{key}; existing content differs."
        )


def _content_type(path: Path) -> str:
    if path.suffix == ".json":
        return "application/json"
    return "application/x-yaml"
