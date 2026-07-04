"""Object-storage helpers for publishing product Floe manifests.

Replaces scripts/local/artifacts/upload-floe-manifest.sh (SeaweedFS via
kubectl port-forward) and upload_floe_manifests_to_s3() from the AWS artifact
deploy script (direct S3). The ops-bucket key layout is identical in both, so
the derivation lives here once.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from olf import k8s, log


@dataclass(frozen=True)
class ManifestUpload:
    path: Path
    key: str


def manifest_key(domain: str, product: str) -> str:
    return f"floe/manifests/{domain}/{product}/{product}.manifest.json"


def discover_tracked_manifests(repo_root: Path) -> list[ManifestUpload]:
    """Source-controlled manifests under domains/*/contracts/floe/manifests/."""
    uploads: list[ManifestUpload] = []
    root = repo_root / "domains"
    for manifest_path in sorted(root.glob("*/contracts/floe/manifests/*.manifest.json")):
        domain = manifest_path.parents[3].name
        product = manifest_path.name[: -len(".manifest.json")]
        uploads.append(ManifestUpload(manifest_path, manifest_key(domain, product)))
    return uploads


def discover_runtime_manifests(manifest_root: Path) -> list[ManifestUpload]:
    """Rendered manifests under <artifact-dir>/manifests/<domain>/<product>.manifest.json."""
    uploads: list[ManifestUpload] = []
    for manifest_path in sorted(manifest_root.glob("*/*.manifest.json")):
        domain = manifest_path.parent.name
        product = manifest_path.name[: -len(".manifest.json")]
        uploads.append(ManifestUpload(manifest_path, manifest_key(domain, product)))
    return uploads


def _put_objects(client, bucket: str, uploads: list[ManifestUpload]) -> None:
    for upload in uploads:
        with upload.path.open("rb") as body:
            client.put_object(
                Bucket=bucket,
                Key=upload.key,
                Body=body,
                ContentType="application/json",
            )
        log.info(f"Published {upload.path} to s3://{bucket}/{upload.key}")


def upload_direct(bucket: str, uploads: list[ManifestUpload], *, region: str | None = None) -> None:
    """Upload with the ambient credential chain (AWS Pod Identity / profile)."""
    client = boto3.client("s3", region_name=region or None)
    _put_objects(client, bucket, uploads)


def upload_via_port_forward(
    bucket: str,
    uploads: list[ManifestUpload],
    *,
    service: str,
    remote_port: int,
    namespace: str,
    access_key_id: str,
    secret_access_key: str,
    region: str,
) -> None:
    """Upload to an in-cluster S3-compatible store through kubectl port-forward."""
    log_path = "/tmp/openlakeforge-seaweedfs-port-forward.log"
    with k8s.port_forward(service, remote_port, namespace, log_path=log_path) as local_port:
        endpoint = f"http://127.0.0.1:{local_port}"
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region,
            config=Config(s3={"addressing_style": "path"}),
        )
        _wait_for_bucket(client, bucket, endpoint)
        _put_objects(client, bucket, uploads)


def _wait_for_bucket(client, bucket: str, endpoint: str, *, attempts: int = 60, delay: float = 2.0) -> None:
    for attempt in range(1, attempts + 1):
        try:
            client.head_bucket(Bucket=bucket)
            return
        except (ClientError, BotoCoreError):
            if attempt == attempts:
                raise RuntimeError(
                    f"bucket '{bucket}' did not become available through {endpoint}."
                ) from None
            time.sleep(delay)
