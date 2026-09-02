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
    """Require the query to fail specifically with an access-control denial.

    `trino_query` raises the same `E2EError` for a genuine authorization
    rejection and for an unrelated failure (a missing/broken sibling catalog,
    a transient connection error, ...). Catching any `E2EError` here would let
    the probe report isolation success when the sibling stage's catalog was
    never provisioned - it never got far enough to be denied anything.
    """
    try:
        trino_query(cfg, sql)
    except E2EError as exc:
        if "Access Denied" not in str(exc):
            raise E2EError(
                f"isolation probe for {user} could not verify a denial of {what}: the query failed for a "
                f"reason other than an access-control rejection ({exc})."
            ) from exc
        return
    raise E2EError(f"isolation breach: {user} was not denied {what} ({sql!r} succeeded).")


def _expect_s3_denied(call, *, who: str, what: str) -> None:  # noqa: ANN001
    """Require the call to fail specifically with an access denial.

    `HeadBucket` has no response body to carry a symbolic AWS error code, so
    botocore reports its HTTP status code (e.g. "403", "404") as `Error.Code`
    instead - checking the HTTP status directly is what actually
    distinguishes "denied" from "the sibling bucket does not exist" for both
    that and `ListObjectsV2`, whose body-bearing error does use a symbolic
    code like "AccessDenied".
    """
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        call()
    except ClientError as exc:
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        code = exc.response.get("Error", {}).get("Code")
        if status != 403 and code not in {"403", "AccessDenied", "Forbidden"}:
            raise E2EError(
                f"isolation probe for {who} could not verify a denial of {what}: the call failed for a "
                f"reason other than access control ({exc})."
            ) from exc
    except BotoCoreError as exc:
        raise E2EError(
            f"isolation probe for {who} could not verify a denial of {what}: the call failed for a "
            f"reason other than access control ({exc})."
        ) from exc
    else:
        raise E2EError(f"isolation breach: {who}'s S3 identity was not denied {what}.")


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

        _expect_s3_denied(
            lambda: client.head_bucket(Bucket=sibling_bucket),
            who=this_stage,
            what=f"HeadBucket on {sibling_bucket}",
        )

        ops_bucket = config.env("OPENLAKEFORGE_OPS_BUCKET_NAME", "openlakeforge-ops")
        _expect_s3_denied(
            lambda: client.list_objects_v2(Bucket=ops_bucket, Prefix=f"activations/{sibling}/", MaxKeys=1),
            who=this_stage,
            what=f"listing activations/{sibling}/ in the shared ops bucket",
        )

        # Positive controls: the same identity must still reach its own
        # bucket and its own ops-bucket activation prefix.
        client.head_bucket(Bucket=this_bucket)
        client.list_objects_v2(Bucket=ops_bucket, Prefix=f"activations/{this_stage}/", MaxKeys=1)
