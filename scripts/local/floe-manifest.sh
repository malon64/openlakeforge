#!/usr/bin/env bash
# Generate the Sales Floe Dagster manifest from the local Kubernetes profile.
set -euo pipefail

CONFIG_PATH="${FLOE_CONFIG_PATH:-domains/sales/contracts/floe/sales_poc.yml}"
PROFILE_PATH="${FLOE_PROFILE_PATH:-domains/sales/contracts/floe/profiles/local-k8s.yml}"
MANIFEST_PATH="${FLOE_MANIFEST_PATH:-domains/sales/contracts/floe/manifests/sales.manifest.json}"
CODE_BUCKET="${OPENLAKEFORGE_CODE_BUCKET:-openlakeforge-code}"
FLOE_ARTIFACT_PREFIX="${OPENLAKEFORGE_FLOE_ARTIFACT_PREFIX:-floe/sales}"

if ! command -v floe &>/dev/null; then
  echo "ERROR: 'floe' not found on PATH. Install it locally, for example with Homebrew, before applying the local stack." >&2
  exit 1
fi

mkdir -p "$(dirname "${MANIFEST_PATH}")"

echo "==> Validating Floe config: ${CONFIG_PATH}"
floe validate -c "${CONFIG_PATH}" -p "${PROFILE_PATH}"

echo "==> Generating Floe manifest: ${MANIFEST_PATH}"
floe manifest generate \
  -c "${CONFIG_PATH}" \
  -p "${PROFILE_PATH}" \
  --output "${MANIFEST_PATH}"

echo "==> Patching manifest artifact URIs for s3://${CODE_BUCKET}/${FLOE_ARTIFACT_PREFIX}"
CODE_BUCKET="${CODE_BUCKET}" FLOE_ARTIFACT_PREFIX="${FLOE_ARTIFACT_PREFIX}" MANIFEST_PATH="${MANIFEST_PATH}" python3 - <<'PY'
import json
import os
from pathlib import Path

bucket = os.environ["CODE_BUCKET"]
prefix = os.environ["FLOE_ARTIFACT_PREFIX"].strip("/")
manifest_path = Path(os.environ["MANIFEST_PATH"])
manifest_uri = f"s3://{bucket}/{prefix}/sales.manifest.json"
config_uri = f"s3://{bucket}/{prefix}/sales_poc.yml"

payload = json.loads(manifest_path.read_text(encoding="utf-8"))
payload["config_uri"] = config_uri
payload["domains"] = []
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

manifest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

echo "Generated ${MANIFEST_PATH}"
