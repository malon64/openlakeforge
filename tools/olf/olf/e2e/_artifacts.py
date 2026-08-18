"""Ops-bucket and Floe-manifest artifact assertions."""

from __future__ import annotations

import time
from typing import Any

import boto3
from botocore.config import Config
from openlakeforge_domain import DomainInventory

from olf import k8s, log, revision
from olf.e2e._dagster import expected_repository_location_names, expected_user_code_pods
from olf.e2e._shell import E2EConfig, E2EError, aws_stack_region, kubectl, load_provider_contracts_or_raise

# The one artifact prefix that is genuinely platform-level, not per-product:
# Dagster's own compute-log archive path. Per-product prefixes (Floe reports,
# dbt artifacts, Floe manifests) come from the domain inventory instead.
ARTIFACT_PREFIXES = ("logs/dagster/compute/",)


def check_ops_artifacts(cfg: E2EConfig) -> None:
    log.step("Checking ops artifact bucket contents...")
    trigger_log_archive_job(cfg)
    deployed_revision = deployed_floe_manifest_revision(cfg)
    provider_contracts = load_provider_contracts_or_raise(cfg)
    bucket = provider_contracts["artifact_bucket"]["bucket_name"]
    if cfg.env == "aws":
        client = boto3.client("s3", region_name=aws_stack_region(cfg))
        assert_ops_artifacts(client, bucket, cfg.namespace, cfg.inventory, deployed_revision)
        return

    assert cfg.seaweedfs_local_port is not None
    access_key_id = k8s.secret_value(
        "seaweedfs-s3-creds",
        "AWS_ACCESS_KEY_ID",
        cfg.namespace,
        kube_context=cfg.kube_context,
    )
    secret_access_key = k8s.secret_value(
        "seaweedfs-s3-creds",
        "AWS_SECRET_ACCESS_KEY",
        cfg.namespace,
        kube_context=cfg.kube_context,
    )
    log_path = f"/tmp/openlakeforge-{cfg.env}-seaweedfs-s3-port-forward.log"
    with k8s.port_forward(
        "seaweedfs-s3",
        8333,
        cfg.namespace,
        local_port=cfg.seaweedfs_local_port,
        log_path=log_path,
        kube_context=cfg.kube_context,
    ):
        endpoint = f"http://127.0.0.1:{cfg.seaweedfs_local_port}"
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="us-east-1",
            config=Config(s3={"addressing_style": "path"}),
        )
        wait_for_bucket(client, bucket, endpoint)
        assert_ops_artifacts(client, bucket, cfg.namespace, cfg.inventory, deployed_revision)


def trigger_log_archive_job(cfg: E2EConfig) -> None:
    archive_job = f"openlakeforge-k8s-log-archive-e2e-{int(time.time())}"
    kubectl(
        cfg,
        [
            "create",
            "job",
            "--from=cronjob/openlakeforge-k8s-log-archive",
            archive_job,
            "-n",
            cfg.namespace,
        ],
    )
    kubectl(
        cfg,
        [
            "wait",
            "--for=condition=complete",
            f"job/{archive_job}",
            "-n",
            cfg.namespace,
            "--timeout=300s",
        ],
    )


def deployed_floe_manifest_revision(cfg: E2EConfig) -> str:
    """Read the Floe revision marker baked into each running Dagster code pod."""
    location_names = expected_repository_location_names(cfg)
    pods = expected_user_code_pods(cfg, location_names)
    if not pods:
        raise E2EError("no running Dagster user-code pods were found to identify the deployed Floe revision.")

    revisions: dict[str, str] = {}
    for pod in pods:
        value = kubectl(
            cfg,
            [
                "exec",
                "-n",
                cfg.namespace,
                pod,
                "--",
                "printenv",
                "OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT",
            ],
            capture=True,
        ).strip()
        if not value:
            raise E2EError(f"Dagster user-code pod {pod} has no built Floe manifest revision marker.")
        revisions[pod] = value

    unique_revisions = set(revisions.values())
    if len(unique_revisions) != 1:
        details = ", ".join(f"{pod}={value}" for pod, value in sorted(revisions.items()))
        raise E2EError(f"Dagster user-code pods disagree on the built Floe manifest revision: {details}.")
    return unique_revisions.pop()


def assert_ops_artifacts(
    client: Any,
    bucket: str,
    namespace: str,
    inventory: DomainInventory,
    deployed_revision: str,
) -> None:
    if deployed_revision == "manual":
        assert_legacy_floe_manifests(client, bucket, inventory)
    else:
        assert_immutable_floe_manifests(client, bucket, inventory, deployed_revision)
    product_prefixes = tuple(
        prefix
        for product in inventory.products
        for prefix in (product.artifact_prefixes.floe_report_prefix, product.artifact_prefixes.dbt_artifact_prefix)
    )
    for prefix in (*product_prefixes, *ARTIFACT_PREFIXES, f"logs/k8s/namespace={namespace}/"):
        require_s3_prefix(client, bucket, prefix)


def assert_immutable_floe_manifests(
    client: Any, bucket: str, inventory: DomainInventory, deployed_revision: str
) -> None:
    try:
        manifest = revision.verify(client, bucket, deployed_revision)
    except revision.RevisionError as exc:
        raise E2EError(f"failed to verify deployed immutable Floe revision {deployed_revision}: {exc}") from exc
    expected_manifest_keys = set(inventory.manifest_keys)
    missing = expected_manifest_keys.difference(manifest.entries)
    if missing:
        raise E2EError(
            f"deployed immutable Floe revision {deployed_revision} does not contain every "
            f"descriptor-discovered product manifest: {', '.join(sorted(missing))}."
        )


def assert_legacy_floe_manifests(client: Any, bucket: str, inventory: DomainInventory) -> None:
    """Verify mutable manifests for a supplied local project-code image."""
    for key in inventory.manifest_keys:
        try:
            client.head_object(Bucket=bucket, Key=key)
        except Exception as exc:
            raise E2EError(f"missing legacy Floe manifest s3://{bucket}/{key}: {exc}") from exc


def wait_for_bucket(client: Any, bucket: str, endpoint: str, *, attempts: int = 60, delay: float = 2.0) -> None:
    for attempt in range(1, attempts + 1):
        try:
            client.head_bucket(Bucket=bucket)
            return
        except Exception as exc:
            if attempt == attempts:
                raise E2EError(f"bucket '{bucket}' did not become available through {endpoint}.") from exc
            time.sleep(delay)


def require_s3_prefix(client: Any, bucket: str, prefix: str) -> None:
    result = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    if not result.get("Contents"):
        raise E2EError(f"expected objects under s3://{bucket}/{prefix}")
