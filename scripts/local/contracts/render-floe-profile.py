#!/usr/bin/env python3
"""Render the local Floe profile from provider contract environment values."""

import os
import sys


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


namespace = env("NAMESPACE", env("OPENLAKEFORGE_KUBE_NAMESPACE", "lakehouse"))
catalog_name = env("OPENLAKEFORGE_CATALOG_LOGICAL_NAME", "iceberg_catalog")
storage_bronze_bucket = env("OPENLAKEFORGE_STORAGE_BRONZE_BUCKET", env("OPENLAKEFORGE_STORAGE_BUCKET", "lakehouse-bronze"))
storage_silver_bucket = env("OPENLAKEFORGE_STORAGE_SILVER_BUCKET", "lakehouse-silver")
ops_bucket = env("OPENLAKEFORGE_OPS_BUCKET_NAME", env("OPENLAKEFORGE_ARTIFACT_BUCKET_NAME", "openlakeforge-ops"))
storage_region = env("OPENLAKEFORGE_STORAGE_REGION", "us-east-1")
storage_implementation = env("OPENLAKEFORGE_STORAGE_IMPLEMENTATION", "storage.s3_compatible.seaweedfs")
is_aws_s3 = storage_implementation == "storage.aws_s3"
storage_endpoint = env("OPENLAKEFORGE_STORAGE_ENDPOINT", "http://seaweedfs-s3:8333")
storage_virtual_endpoint = env(
    "OPENLAKEFORGE_STORAGE_VIRTUAL_HOST_ENDPOINT",
    f"http://{namespace}.svc.cluster.local:8333",
)
catalog_type = env("OPENLAKEFORGE_CATALOG_TYPE", "rest")
catalog_provider = env("OPENLAKEFORGE_CATALOG_PROVIDER", "polaris")
catalog_rest_uri = env("OPENLAKEFORGE_CATALOG_REST_URI", "http://polaris:8181/api/catalog")
catalog_glue_rest_uri = env("OPENLAKEFORGE_CATALOG_GLUE_REST_URI", catalog_rest_uri)
catalog_glue_region = env("OPENLAKEFORGE_CATALOG_GLUE_REGION", storage_region)
catalog_glue_catalog_id = env("OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID", "")
catalog_token_uri = env(
    "OPENLAKEFORGE_CATALOG_TOKEN_URI",
    "http://polaris:8181/api/catalog/v1/oauth/tokens",
)
catalog_warehouse = env("OPENLAKEFORGE_CATALOG_WAREHOUSE", env("OPENLAKEFORGE_CATALOG_NAME", "lakehouse_dev"))
catalog_scope = env("OPENLAKEFORGE_CATALOG_OAUTH_SCOPE", "PRINCIPAL_ROLE:ALL")
floe_image = env("FLOE_IMAGE", "ghcr.io/malon64/floe:0.5.4")
storage_secret = env("OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME", "" if is_aws_s3 else "seaweedfs-s3-creds")
storage_access_key = env("OPENLAKEFORGE_STORAGE_ACCESS_KEY_ID_KEY", "AWS_ACCESS_KEY_ID")
storage_secret_key = env("OPENLAKEFORGE_STORAGE_SECRET_ACCESS_KEY_KEY", "AWS_SECRET_ACCESS_KEY")
catalog_secret = env("OPENLAKEFORGE_CATALOG_FLOE_CREDENTIALS_SECRET_NAME", "polaris-floe-creds")
catalog_client_id_key = env("OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID_KEY", "POLARIS_FLOE_CLIENT_ID")
catalog_client_secret_key = env("OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET_KEY", "POLARIS_FLOE_CLIENT_SECRET")

runner_env = {
    "AWS_REGION": storage_region,
    "AWS_DEFAULT_REGION": storage_region,
    "AWS_S3_FORCE_PATH_STYLE": env("OPENLAKEFORGE_STORAGE_PATH_STYLE_ACCESS", "true"),
    "AWS_EC2_METADATA_DISABLED": "true",
}
if env("OPENLAKEFORGE_STORAGE_SSL_MODE", "disabled") == "disabled":
    runner_env["AWS_ALLOW_HTTP"] = "true"
if storage_endpoint:
    runner_env["AWS_ENDPOINT_URL"] = storage_endpoint
if storage_virtual_endpoint:
    runner_env["AWS_ENDPOINT_URL_S3"] = storage_virtual_endpoint

runner_env_yaml = "\n".join(f"      {key}: {value}" for key, value in runner_env.items())

storage_secret_yaml = ""
if storage_secret:
    storage_secret_yaml = f"""      - name: AWS_ACCESS_KEY_ID
        secret_name: {storage_secret}
        key: {storage_access_key}
      - name: AWS_SECRET_ACCESS_KEY
        secret_name: {storage_secret}
        key: {storage_secret_key}
"""

catalog_secret_yaml = ""
if catalog_type == "rest" and catalog_secret:
    catalog_secret_yaml = f"""      - name: OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID
        secret_name: {catalog_secret}
        key: {catalog_client_id_key}
      - name: OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET
        secret_name: {catalog_secret}
        key: {catalog_client_secret_key}
"""

if catalog_type == "rest":
    catalog_definition = f"""    - name: "{catalog_name}"
      type: "rest"
      uri: "{catalog_rest_uri}"
      credential: "client_credentials:${{OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID}}:${{OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET}}"
      warehouse: "{catalog_warehouse}"
      oauth2_server_uri: "{catalog_token_uri}"
      scope: "{catalog_scope}"
      warehouse_storage: "lakehouse_silver"
      warehouse_prefix: "s3://{storage_silver_bucket}"
"""
elif catalog_type == "glue" and catalog_provider == "aws-glue":
    catalog_definition = f"""    - name: "{catalog_name}"
      type: "rest"
      uri: "{catalog_glue_rest_uri}"
      warehouse: "{catalog_glue_catalog_id or catalog_warehouse}"
      authorization_type: "sigv4"
      signing_name: "glue"
      signing_region: "{catalog_glue_region}"
      warehouse_storage: "lakehouse_silver"
      warehouse_prefix: "s3://{storage_silver_bucket}"
"""
else:
    raise SystemExit(f"ERROR: unsupported Floe catalog_type/provider: {catalog_type!r}/{catalog_provider!r}.")

sys.stdout.write(
    f"""apiVersion: floe/v1
kind: EnvironmentProfile
metadata:
  name: local-k8s
  env: local
  description: "Contract-rendered local Kubernetes runner profile for OpenLakeForge Floe assets."
storages:
  default: "lakehouse_bronze"
  definitions:
    - name: "lakehouse_bronze"
      type: "s3"
      bucket: "{storage_bronze_bucket}"
      region: "{storage_region}"
    - name: "lakehouse_silver"
      type: "s3"
      bucket: "{storage_silver_bucket}"
      region: "{storage_region}"
    - name: "openlakeforge_ops"
      type: "s3"
      bucket: "{ops_bucket}"
      region: "{storage_region}"
catalogs:
  default: "{catalog_name}"
  definitions:
{catalog_definition.rstrip()}
execution:
  orchestration:
    strategy: sequential
  runner:
    type: kubernetes_job
    image: {floe_image}
    namespace: {namespace}
    service_account: dagster
    timeout_seconds: 600
    ttl_seconds_after_finished: 3600
    poll_interval_seconds: 5
    env:
{runner_env_yaml}
    secrets:
{storage_secret_yaml.rstrip()}
{catalog_secret_yaml.rstrip()}
validation:
  strict: true
"""
)
