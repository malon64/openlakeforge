"""The artifact revision contract: one content hash across image, manifests, and Dagster env.

Dagster's `project-code` image and the Floe runner pods it launches must agree
on exactly which generated Floe manifest set they run. This module computes a
deterministic content hash over the set of rendered Floe runtime artifacts
(the same set `olf.s3.discover_runtime_artifacts` uploads), publishes it as a
sidecar object alongside the uploaded artifacts, and recomputes/verifies it
against the ops bucket so an interrupted or manual deploy fails fast and
names the exact artifact that diverged, instead of silently mixing revisions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from botocore.exceptions import BotoCoreError, ClientError

from olf import s3

REVISION_SIDECAR_KEY = "floe/manifests/REVISION"

# S3 object metadata keys are lower-cased and exposed to callers with an
# "x-amz-meta-" prefix; boto3's Metadata dict takes the bare key.
REVISION_METADATA_KEY = "olf-floe-revision"

DEFAULT_BUCKET_PREFIXES = ("floe/configs/", "floe/profiles/", "floe/manifests/")


def prefixes_present(entries: dict[str, str]) -> tuple[str, ...]:
    """The managed-prefix subset actually represented in a set of entries.

    `discover_tracked_manifests` (the default `olf artifacts upload-manifests
    --via port-forward` path, i.e. `make floe-manifest-upload`) returns only
    `floe/manifests/...` keys -- no configs or profiles. Pruning with the full
    `DEFAULT_BUCKET_PREFIXES` against that manifest-only entry set would
    reconcile `floe/configs/` and `floe/profiles/` against zero declared
    entries and delete every object under them, even though this invocation
    never had an opinion on configs/profiles at all. Scope the prune to only
    the prefixes this upload actually declares.
    """
    found = set()
    for key in entries:
        parts = key.split("/")
        if len(parts) >= 2:
            found.add(f"{parts[0]}/{parts[1]}/")
    return tuple(sorted(found))


class RevisionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RevisionManifest:
    """The aggregate revision plus the per-key content hash it was built from."""

    revision: str
    entries: dict[str, str]

    def to_json(self) -> str:
        payload = {"revision": self.revision, "entries": dict(sorted(self.entries.items()))}
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    @staticmethod
    def from_json(data: str) -> RevisionManifest:
        try:
            payload = json.loads(data)
            return RevisionManifest(
                revision=payload["revision"],
                entries=dict(payload.get("entries") or {}),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RevisionError(f"malformed {REVISION_SIDECAR_KEY} sidecar: {exc}") from exc


@dataclass(frozen=True)
class RevisionDiff:
    """A diagnosable comparison between an expected and an actual artifact set."""

    expected_revision: str
    actual_revision: str
    missing_keys: tuple[str, ...] = ()
    extra_keys: tuple[str, ...] = ()
    changed_keys: tuple[str, ...] = ()

    @property
    def matches(self) -> bool:
        return (
            self.expected_revision == self.actual_revision
            and not self.missing_keys
            and not self.extra_keys
            and not self.changed_keys
        )

    def describe(self) -> str:
        if self.matches:
            return f"artifact revision {self.actual_revision} matches expected {self.expected_revision}."
        lines = [
            "artifact revision mismatch: "
            f"expected {self.expected_revision}, actual {self.actual_revision}."
        ]
        if self.missing_keys:
            lines.append("  missing (expected but not found): " + ", ".join(self.missing_keys))
        if self.extra_keys:
            lines.append("  extra (found but not expected): " + ", ".join(self.extra_keys))
        if self.changed_keys:
            lines.append("  changed (content hash differs): " + ", ".join(self.changed_keys))
        return "\n".join(lines)


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _aggregate(entries: dict[str, str]) -> str:
    hasher = hashlib.sha256()
    for key in sorted(entries):
        hasher.update(f"{key}\0{entries[key]}\n".encode())
    return f"sha256:{hasher.hexdigest()}"


def manifest_from_entries(entries: dict[str, str]) -> RevisionManifest:
    return RevisionManifest(revision=_aggregate(entries), entries=entries)


def manifest_from_uploads(uploads: Sequence[s3.ManifestUpload]) -> RevisionManifest:
    """Hash the exact set of local files an artifact upload is about to publish."""
    entries = {upload.key: _hash_bytes(upload.path.read_bytes()) for upload in uploads}
    return manifest_from_entries(entries)


def compute_revision_manifest(runtime_root: Path) -> RevisionManifest:
    """Hash the set of rendered Floe runtime artifacts under a runtime artifact root.

    Sorts by ops-bucket key (reusing `olf.s3.discover_runtime_artifacts` so the
    hashed set is exactly the uploaded set) and hashes ``key + "\\0" +
    sha256(bytes) + "\\n"`` per entry, so the result is deterministic and
    independent of filesystem path/traversal order.
    """
    uploads = s3.discover_runtime_artifacts(runtime_root)
    if not uploads:
        raise RevisionError(f"no rendered Floe runtime artifacts found under {runtime_root}.")
    return manifest_from_uploads(uploads)


def compute_revision(runtime_root: Path) -> str:
    return compute_revision_manifest(runtime_root).revision


def diff_revisions(expected: RevisionManifest, actual: RevisionManifest) -> RevisionDiff:
    expected_keys = set(expected.entries)
    actual_keys = set(actual.entries)
    missing = tuple(sorted(expected_keys - actual_keys))
    extra = tuple(sorted(actual_keys - expected_keys))
    changed = tuple(
        sorted(key for key in expected_keys & actual_keys if expected.entries[key] != actual.entries[key])
    )
    return RevisionDiff(
        expected_revision=expected.revision,
        actual_revision=actual.revision,
        missing_keys=missing,
        extra_keys=extra,
        changed_keys=changed,
    )


# --- S3 sidecar helpers ------------------------------------------------------


def prune_orphaned_objects(
    client,
    bucket: str,
    manifest: RevisionManifest,
    *,
    prefixes: tuple[str, ...] = DEFAULT_BUCKET_PREFIXES,
) -> list[str]:
    """Delete bucket objects under the managed prefixes that the new manifest no longer declares.

    Uploads only overwrite keys present in the current local artifact set; a
    renamed or removed product's old object otherwise survives forever. Once
    that happens `revision verify` (and the e2e artifact-revision check) fail
    permanently, because the bucket's recomputed hash includes an object the
    sidecar never declared and never will again. Returns the deleted keys.
    """
    declared = set(manifest.entries)
    orphaned = sorted(
        key
        for prefix in prefixes
        for key in _list_keys(client, bucket, prefix)
        if key != REVISION_SIDECAR_KEY and key not in declared
    )
    for start in range(0, len(orphaned), 1000):  # S3 DeleteObjects caps at 1000 keys per call
        batch = orphaned[start : start + 1000]
        client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": key} for key in batch]})
    return orphaned


def publish_sidecar(
    client,
    bucket: str,
    manifest: RevisionManifest,
    *,
    prune: bool = True,
    prefixes: tuple[str, ...] = DEFAULT_BUCKET_PREFIXES,
) -> list[str]:
    """Publish the revision sidecar, reconciling stale objects first by default.

    Pruning runs before the sidecar write so a crash mid-prune still leaves the
    previous sidecar pointing at a bucket state `revision verify` can still
    explain, rather than a freshly-published sidecar describing a bucket that
    has not actually been reconciled yet.
    """
    deleted = prune_orphaned_objects(client, bucket, manifest, prefixes=prefixes) if prune else []
    client.put_object(
        Bucket=bucket,
        Key=REVISION_SIDECAR_KEY,
        Body=manifest.to_json().encode("utf-8"),
        ContentType="application/json",
        Metadata={REVISION_METADATA_KEY: manifest.revision},
    )
    return deleted


def fetch_sidecar(client, bucket: str) -> RevisionManifest:
    try:
        response = client.get_object(Bucket=bucket, Key=REVISION_SIDECAR_KEY)
    except (BotoCoreError, ClientError) as exc:
        raise RevisionError(
            f"no {REVISION_SIDECAR_KEY} sidecar found in s3://{bucket}/ "
            "(the deploy may have been interrupted before manifests were published, "
            "or 'olf revision publish' was never run)."
        ) from exc
    body = response["Body"].read()
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    return RevisionManifest.from_json(body)


def _fetch_bucket_objects(
    client,
    bucket: str,
    prefixes: tuple[str, ...],
) -> dict[str, tuple[bytes, dict[str, str]]]:
    """Return ``{key: (content, metadata)}`` for every object under the given prefixes."""
    objects: dict[str, tuple[bytes, dict[str, str]]] = {}
    for prefix in prefixes:
        for key in _list_keys(client, bucket, prefix):
            if key == REVISION_SIDECAR_KEY:
                continue
            response = client.get_object(Bucket=bucket, Key=key)
            content = response["Body"].read()
            metadata = dict(response.get("Metadata") or {})
            objects[key] = (content, metadata)
    return objects


def fetch_bucket_manifest(
    client,
    bucket: str,
    *,
    prefixes: tuple[str, ...] = DEFAULT_BUCKET_PREFIXES,
) -> RevisionManifest:
    """Recompute a RevisionManifest directly from the objects currently in the bucket."""
    objects = _fetch_bucket_objects(client, bucket, prefixes)
    if not objects:
        raise RevisionError(f"no Floe runtime artifacts found under s3://{bucket}/{{{', '.join(prefixes)}}}.")
    entries = {key: _hash_bytes(content) for key, (content, _metadata) in objects.items()}
    return manifest_from_entries(entries)


def _list_keys(client, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents") or []:
            keys.append(obj["Key"])
    return sorted(keys)


def verify(client, bucket: str, expected: str) -> RevisionDiff:
    """Verify the bucket's currently-uploaded artifact set against an expected revision.

    Recomputes the actual artifact-set revision directly from the objects
    currently in the bucket. On a mismatch, names every object whose own
    ``x-amz-meta-olf-floe-revision`` upload-time tag does not match
    ``expected`` -- exactly the objects an interrupted or partial deploy left
    behind at a stale revision (a deploy killed before any upload ran leaves
    every object stale; a deploy killed mid-upload leaves only the
    not-yet-reuploaded objects stale).
    """
    objects = _fetch_bucket_objects(client, bucket, DEFAULT_BUCKET_PREFIXES)
    if not objects:
        raise RevisionError(
            f"no Floe runtime artifacts found under s3://{bucket}/{{{', '.join(DEFAULT_BUCKET_PREFIXES)}}}."
        )
    entries = {key: _hash_bytes(content) for key, (content, _metadata) in objects.items()}
    actual = manifest_from_entries(entries)
    if actual.revision == expected:
        return RevisionDiff(expected_revision=expected, actual_revision=actual.revision)

    changed_keys = tuple(
        sorted(key for key, (_content, metadata) in objects.items() if metadata.get(REVISION_METADATA_KEY) != expected)
    )
    return RevisionDiff(expected_revision=expected, actual_revision=actual.revision, changed_keys=changed_keys)
