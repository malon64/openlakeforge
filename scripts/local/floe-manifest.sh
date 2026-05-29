#!/usr/bin/env bash
# Generate the Sales Floe Dagster manifest from the local Kubernetes profile.
set -euo pipefail

CONFIG_PATH="${FLOE_CONFIG_PATH:-domains/sales/contracts/floe/sales_poc.yml}"
PROFILE_PATH="${FLOE_PROFILE_PATH:-domains/sales/contracts/floe/profiles/local-k8s.yml}"
MANIFEST_PATH="${FLOE_MANIFEST_PATH:-domains/sales/contracts/floe/manifests/sales.manifest.json}"
CODE_BUCKET="${OPENLAKEFORGE_CODE_BUCKET:-openlakeforge-code}"
FLOE_ARTIFACT_PREFIX="${OPENLAKEFORGE_FLOE_ARTIFACT_PREFIX:-floe/sales}"

FLOE_CMD=(floe)
if ! command -v floe &>/dev/null || [[ "$(floe --version 2>/dev/null || true)" != "floe 0.4.4" ]]; then
  if ! command -v docker &>/dev/null; then
    echo "ERROR: Floe 0.4.4 is required on PATH, or Docker must be available to run ghcr.io/malon64/floe:0.4.4." >&2
    exit 1
  fi
  FLOE_CMD=(docker run --rm -v "${PWD}:/work" -w /work ghcr.io/malon64/floe:0.4.4)
fi

mkdir -p "$(dirname "${MANIFEST_PATH}")"

echo "==> Validating Floe config: ${CONFIG_PATH}"
"${FLOE_CMD[@]}" validate -c "${CONFIG_PATH}" -p "${PROFILE_PATH}"

echo "==> Generating Floe manifest: ${MANIFEST_PATH}"
"${FLOE_CMD[@]}" manifest generate \
  -c "${CONFIG_PATH}" \
  -p "${PROFILE_PATH}" \
  --output "${MANIFEST_PATH}"

echo "==> Patching manifest artifact URIs for s3://${CODE_BUCKET}/${FLOE_ARTIFACT_PREFIX}"
CODE_BUCKET="${CODE_BUCKET}" FLOE_ARTIFACT_PREFIX="${FLOE_ARTIFACT_PREFIX}" MANIFEST_PATH="${MANIFEST_PATH}" CONFIG_PATH="${CONFIG_PATH}" python3 - <<'PY'
import json
import os
from pathlib import Path

import yaml

bucket = os.environ["CODE_BUCKET"]
prefix = os.environ["FLOE_ARTIFACT_PREFIX"].strip("/")
manifest_path = Path(os.environ["MANIFEST_PATH"])
config_path = Path(os.environ["CONFIG_PATH"])
manifest_uri = f"s3://{bucket}/{prefix}/sales.manifest.json"
config_uri = f"s3://{bucket}/{prefix}/sales_poc.yml"

payload = json.loads(manifest_path.read_text(encoding="utf-8"))
config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
payload["config_uri"] = config_uri
payload["domains"] = []

if config.get("storages"):
    payload["storages"] = {
        "default": config["storages"].get("default"),
        "definitions": [
            {
                "name": storage.get("name"),
                "fs_type": storage.get("type"),
                "bucket": storage.get("bucket"),
                "region": storage.get("region"),
                "account": storage.get("account"),
                "container": storage.get("container"),
                "prefix": storage.get("prefix"),
            }
            for storage in config["storages"].get("definitions", [])
        ],
    }

if config.get("catalogs"):
    storage_by_name = {
        storage.get("name"): storage
        for storage in config.get("storages", {}).get("definitions", [])
    }
    catalog_definitions = []
    for catalog in config["catalogs"].get("definitions", []):
        type_config = {
            key: value
            for key, value in catalog.items()
            if key
            not in {
                "name",
                "warehouse_storage",
                "warehouse_prefix",
            }
        }
        catalog_definitions.append(
            {
                "name": catalog.get("name"),
                "type_config": type_config,
                "warehouse_storage": catalog.get("warehouse_storage"),
                "warehouse_prefix": catalog.get("warehouse_prefix"),
            }
        )
        warehouse_storage = catalog.get("warehouse_storage")
        warehouse_prefix = catalog_definitions[-1]["warehouse_prefix"]
        storage = storage_by_name.get(warehouse_storage)
        if (
            storage
            and storage.get("type") == "s3"
            and storage.get("bucket")
            and warehouse_prefix
            and "://" not in warehouse_prefix
        ):
            catalog_definitions[-1]["warehouse_prefix"] = f"s3://{storage['bucket']}"
    payload["catalogs"] = {
        "default": config["catalogs"].get("default"),
        "definitions": catalog_definitions,
    }

payload["execution"]["base_args"] = [
    "run",
    "--manifest",
    manifest_uri,
    "--log-format",
    "json",
    "--quiet",
]

for entity in payload["entities"]:
    entity["domain"] = "sales"
    entity["group_name"] = "sales"
    entity["asset_key"] = ["sales", entity["name"]]
    entity["source"]["path"] = entity["source"]["uri"]
    for sink in entity["sinks"].values():
        if sink and sink.get("uri"):
            sink["path"] = sink["uri"]

manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

echo "Generated ${MANIFEST_PATH}"
