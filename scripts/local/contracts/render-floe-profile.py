#!/usr/bin/env python3
"""Render the local Floe profile from provider contract environment values."""

import os
import sys


def env(name: str, default: str) -> str:
    return os.environ.get(name, default)


namespace = env("NAMESPACE", env("OPENLAKEFORGE_KUBE_NAMESPACE", "lakehouse"))
storage_name = env("OPENLAKEFORGE_STORAGE_LOGICAL_NAME", "lakehouse_storage")
catalog_name = env("OPENLAKEFORGE_CATALOG_LOGICAL_NAME", "iceberg_catalog")
storage_bucket = env("OPENLAKEFORGE_STORAGE_BUCKET", "iceberg-data")
storage_region = env("OPENLAKEFORGE_STORAGE_REGION", "us-east-1")
storage_endpoint = env("OPENLAKEFORGE_STORAGE_ENDPOINT", "http://seaweedfs-s3:8333")
storage_virtual_endpoint = env(
    "OPENLAKEFORGE_STORAGE_VIRTUAL_HOST_ENDPOINT",
    f"http://{namespace}.svc.cluster.local:8333",
)
catalog_type = env("OPENLAKEFORGE_CATALOG_TYPE", "rest")
catalog_rest_uri = env("OPENLAKEFORGE_CATALOG_REST_URI", "http://polaris:8181/api/catalog")
catalog_token_uri = env(
    "OPENLAKEFORGE_CATALOG_TOKEN_URI",
    "http://polaris:8181/api/catalog/v1/oauth/tokens",
)
catalog_warehouse = env("OPENLAKEFORGE_CATALOG_WAREHOUSE", env("OPENLAKEFORGE_CATALOG_NAME", "lakehouse_dev"))
catalog_scope = env("OPENLAKEFORGE_CATALOG_OAUTH_SCOPE", "PRINCIPAL_ROLE:ALL")
floe_image = env("FLOE_IMAGE", "ghcr.io/malon64/floe:0.5.4")
storage_secret = env("OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME", "seaweedfs-s3-creds")
storage_access_key = env("OPENLAKEFORGE_STORAGE_ACCESS_KEY_ID_KEY", "AWS_ACCESS_KEY_ID")
storage_secret_key = env("OPENLAKEFORGE_STORAGE_SECRET_ACCESS_KEY_KEY", "AWS_SECRET_ACCESS_KEY")
catalog_secret = env("OPENLAKEFORGE_CATALOG_FLOE_CREDENTIALS_SECRET_NAME", "polaris-floe-creds")
catalog_client_id_key = env("OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID_KEY", "POLARIS_FLOE_CLIENT_ID")
catalog_client_secret_key = env("OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET_KEY", "POLARIS_FLOE_CLIENT_SECRET")

if catalog_type != "rest":
    raise SystemExit(f"ERROR: Floe local profile supports catalog_type=rest only, got {catalog_type!r}.")

sys.stdout.write(
    f"""apiVersion: floe/v1
kind: EnvironmentProfile
metadata:
  name: local-k8s
  env: local
  description: "Contract-rendered local Kubernetes runner profile for OpenLakeForge Floe assets."
storages:
  default: "{storage_name}"
  definitions:
    - name: "{storage_name}"
      type: "s3"
      bucket: "{storage_bucket}"
      region: "{storage_region}"
catalogs:
  default: "{catalog_name}"
  definitions:
    - name: "{catalog_name}"
      type: "rest"
      uri: "{catalog_rest_uri}"
      credential: "client_credentials:${{OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID}}:${{OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET}}"
      warehouse: "{catalog_warehouse}"
      oauth2_server_uri: "{catalog_token_uri}"
      scope: "{catalog_scope}"
      warehouse_storage: "{storage_name}"
      warehouse_prefix: "s3://{storage_bucket}"
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
      AWS_REGION: {storage_region}
      AWS_DEFAULT_REGION: {storage_region}
      AWS_ENDPOINT_URL: {storage_endpoint}
      AWS_ENDPOINT_URL_S3: {storage_virtual_endpoint}
      AWS_S3_FORCE_PATH_STYLE: "{env("OPENLAKEFORGE_STORAGE_PATH_STYLE_ACCESS", "true")}"
      AWS_ALLOW_HTTP: "{str(env("OPENLAKEFORGE_STORAGE_SSL_MODE", "disabled") == "disabled").lower()}"
      AWS_EC2_METADATA_DISABLED: "true"
    secrets:
      - name: AWS_ACCESS_KEY_ID
        secret_name: {storage_secret}
        key: {storage_access_key}
      - name: AWS_SECRET_ACCESS_KEY
        secret_name: {storage_secret}
        key: {storage_secret_key}
      - name: OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID
        secret_name: {catalog_secret}
        key: {catalog_client_id_key}
      - name: OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET
        secret_name: {catalog_secret}
        key: {catalog_client_secret_key}
validation:
  strict: true
"""
)
