"""Cross-stage isolation probes.

Proves the acceptance criterion #114 exists for: "DEV runtime configuration
cannot read/write PROD, and vice versa." Every probe is paired with a
positive control against the caller's own stage, so the suite cannot pass
merely because the cluster is broken rather than because isolation holds.

Local-only today: local is the only stage-aware root with more than one
stage actually provisioned in this repository's verification profile. A
deployment with only one enabled stage has nothing to isolate against, so
this module skips cleanly rather than failing.
"""

from __future__ import annotations

from typing import Any

from olf import config, k8s, log
from olf.e2e._shell import E2EConfig, E2EError, load_provider_contracts_or_raise
from olf.e2e._trino import trino_query


def _sibling_stage(cfg: E2EConfig) -> str | None:
    """The other enabled stage to probe isolation against, or None if there
    isn't one (single-stage deployment - nothing to isolate)."""
    this_stage = cfg.namespace.removeprefix("olf-")
    candidates = ("dev", "prod", "uat")
    for candidate in candidates:
        if candidate == this_stage:
            continue
        if k8s.resource_exists("namespace", f"olf-{candidate}", f"olf-{candidate}"):
            return candidate
    return None


def _expect_trino_denied(cfg: E2EConfig, *, user: str, sql: str, what: str) -> None:
    try:
        trino_query(cfg, sql)
    except E2EError:
        return
    raise E2EError(f"isolation breach: {user} was not denied {what} ({sql!r} succeeded).")


def _s3_identity(namespace: str, secret_name: str) -> tuple[str, str] | None:
    try:
        access_key = k8s.secret_value(secret_name, "AWS_ACCESS_KEY_ID", namespace)
        secret_key = k8s.secret_value(secret_name, "AWS_SECRET_ACCESS_KEY", namespace)
    except k8s.KubectlError:
        return None
    return access_key, secret_key


def _s3_client(access_key: str, secret_key: str, *, local_port: int, region: str):
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=f"http://127.0.0.1:{local_port}",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(s3={"addressing_style": "path"}),
    )


def _bronze_bucket_for_stage(provider_contracts: dict[str, Any], stage: str) -> str:
    try:
        bucket = provider_contracts["stages"][stage]["storage"]["bronze"]["bucket_name"]
    except (KeyError, TypeError) as exc:
        raise E2EError(
            f"provider_contracts.stages.{stage}.storage.bronze.bucket_name is required for isolation."
        ) from exc
    if not isinstance(bucket, str) or not bucket:
        raise E2EError(f"provider_contracts.stages.{stage}.storage.bronze.bucket_name must be a non-empty string.")
    return bucket


def check_stage_isolation(cfg: E2EConfig) -> None:
    """Prove this stage's runtime identity cannot read/write the sibling
    stage's Trino catalog, S3 buckets, or ops-bucket activation prefix -
    and, as a positive control, that it *can* reach its own."""
    if cfg.env != "local":
        log.info(f"Skipping stage isolation probe: not yet wired for {cfg.env}.")
        return
    sibling = _sibling_stage(cfg)
    if sibling is None:
        log.info("Skipping stage isolation probe: only one stage is enabled.")
        return

    this_stage = cfg.namespace.removeprefix("olf-")
    provider_contracts = load_provider_contracts_or_raise(cfg)
    this_bucket = _bronze_bucket_for_stage(provider_contracts, this_stage)
    sibling_bucket = _bronze_bucket_for_stage(provider_contracts, sibling)
    runtime_user = f"{cfg.namespace}-runtime"
    log.step(f"Checking Trino cross-stage isolation ({this_stage} -> {sibling})...")
    _expect_trino_denied(
        cfg,
        user=runtime_user,
        sql=f"SELECT 1 FROM lakehouse_{sibling}.information_schema.tables LIMIT 1",
        what=f"read access to lakehouse_{sibling}",
    )
    # Positive control: the same identity must still reach its own catalog.
    trino_query(cfg, f"SELECT 1 FROM lakehouse_{this_stage}.information_schema.tables LIMIT 1")

    log.step(f"Checking SeaweedFS cross-stage isolation ({this_stage} -> {sibling})...")
    identity = _s3_identity(cfg.namespace, f"seaweedfs-{this_stage}-s3-creds")
    if identity is None:
        raise E2EError(f"stage isolation probe: seaweedfs-{this_stage}-s3-creds not found in {cfg.namespace}.")
    access_key, secret_key = identity
    region = config.env("OPENLAKEFORGE_STORAGE_REGION", "us-east-1")
    log_prefix = config.env("OPENLAKEFORGE_PORT_FORWARD_LOG_PREFIX", "/tmp/openlakeforge")
    with k8s.port_forward(
        "seaweedfs-s3", 8333, cfg.shared_namespace or "olf-system", log_path=f"{log_prefix}-isolation-s3.log"
    ) as local_port:
        client = _s3_client(access_key, secret_key, local_port=local_port, region=region)
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            client.head_bucket(Bucket=sibling_bucket)
        except (ClientError, BotoCoreError):
            pass
        else:
            raise E2EError(
                f"isolation breach: {this_stage}'s S3 identity was not denied HeadBucket on {sibling_bucket}."
            )

        ops_bucket = config.env("OPENLAKEFORGE_OPS_BUCKET_NAME", "openlakeforge-ops")
        try:
            client.list_objects_v2(Bucket=ops_bucket, Prefix=f"activations/{sibling}/", MaxKeys=1)
        except (ClientError, BotoCoreError):
            pass
        else:
            raise E2EError(
                f"isolation breach: {this_stage}'s S3 identity was not denied listing "
                f"activations/{sibling}/ in the shared ops bucket."
            )

        # Positive controls: the same identity must still reach its own
        # bucket and its own ops-bucket activation prefix.
        client.head_bucket(Bucket=this_bucket)
        client.list_objects_v2(Bucket=ops_bucket, Prefix=f"activations/{this_stage}/", MaxKeys=1)
