#!/usr/bin/env bash
# Deploy source-controlled OpenMetadata domain and data-product assets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
NAMESPACE="${NAMESPACE:-lakehouse}"

# shellcheck source=/dev/null
source "${REPO_ROOT}/scripts/local/contracts/load-runtime-env.sh"

OPENMETADATA_SERVICE="${OPENMETADATA_SERVICE:-openmetadata}"
OPENMETADATA_SERVICE_PORT="${OPENMETADATA_SERVICE_PORT:-8585}"
OPENMETADATA_LOCAL_PORT="${OPENMETADATA_LOCAL_PORT:-18585}"
OPENMETADATA_ADMIN_EMAIL="${OPENMETADATA_ADMIN_EMAIL:-admin@open-metadata.org}"
OPENMETADATA_ADMIN_PASSWORD="${OPENMETADATA_ADMIN_PASSWORD:-admin}"
OPENMETADATA_METADATA_ROOT="${OPENMETADATA_METADATA_ROOT:-domains}"
OPENMETADATA_METADATA_SOURCE_DIR="${OPENMETADATA_METADATA_SOURCE_DIR:-}"
OPENMETADATA_ALLOW_MISSING_ASSETS="${OPENMETADATA_ALLOW_MISSING_ASSETS:-false}"
OPENMETADATA_CATALOG_SERVICE="${OPENMETADATA_CATALOG_SERVICE:-${OPENLAKEFORGE_CATALOG_PROVIDER}}"
OPENMETADATA_CATALOG_DATABASE="${OPENMETADATA_CATALOG_DATABASE:-${OPENLAKEFORGE_CATALOG_NAME}}"
OPENMETADATA_CLEANUP_LEGACY_DEFAULT_DATABASE="${OPENMETADATA_CLEANUP_LEGACY_DEFAULT_DATABASE:-true}"

for cmd in kubectl python3; do
  if ! command -v "${cmd}" &>/dev/null; then
    echo "ERROR: '${cmd}' not found on PATH" >&2
    exit 1
  fi
done

PYTHON_BIN="python3"
if ! "${PYTHON_BIN}" - <<'PY'
try:
    import yaml  # noqa: F401
except ImportError:
    raise SystemExit(1)
PY
then
  PYTHON_VENV="${REPO_ROOT}/.tmp/python/openmetadata-metadata"
  echo "==> Python package 'PyYAML' not found; preparing local helper environment..."
  if [[ ! -x "${PYTHON_VENV}/bin/python" ]]; then
    python3 -m venv "${PYTHON_VENV}"
  fi
  "${PYTHON_VENV}/bin/python" -m pip install --upgrade pip >/dev/null
  "${PYTHON_VENV}/bin/python" -m pip install "PyYAML>=6,<7" >/dev/null
  PYTHON_BIN="${PYTHON_VENV}/bin/python"
fi

echo "==> Waiting for OpenMetadata deployment..."
kubectl rollout status "deployment/${OPENMETADATA_SERVICE}" -n "${NAMESPACE}" --timeout=300s

kubectl port-forward "svc/${OPENMETADATA_SERVICE}" \
  "${OPENMETADATA_LOCAL_PORT}:${OPENMETADATA_SERVICE_PORT}" \
  -n "${NAMESPACE}" >/tmp/openlakeforge-openmetadata-port-forward.log 2>&1 &
port_forward_pid="$!"
cleanup() {
  kill "${port_forward_pid}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

"${PYTHON_BIN}" - \
  "http://127.0.0.1:${OPENMETADATA_LOCAL_PORT}" \
  "${OPENMETADATA_ADMIN_EMAIL}" \
  "${OPENMETADATA_ADMIN_PASSWORD}" \
  "${OPENMETADATA_METADATA_ROOT}" \
  "${OPENMETADATA_METADATA_SOURCE_DIR}" \
  "${OPENMETADATA_ALLOW_MISSING_ASSETS}" \
  "${OPENMETADATA_CATALOG_SERVICE}" \
  "${OPENMETADATA_CATALOG_DATABASE}" \
  "${OPENMETADATA_CLEANUP_LEGACY_DEFAULT_DATABASE}" <<'PY'
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

BASE_URL = sys.argv[1].rstrip("/")
ADMIN_EMAIL = sys.argv[2]
ADMIN_PASSWORD = sys.argv[3]
METADATA_ROOT = Path(sys.argv[4])
METADATA_SOURCE_DIR = sys.argv[5]
ALLOW_MISSING_ASSETS = sys.argv[6].lower() in {"1", "true", "yes", "y"}
CATALOG_SERVICE = sys.argv[7]
CATALOG_DATABASE = sys.argv[8]
CLEANUP_LEGACY_DEFAULT_DATABASE = sys.argv[9].lower() in {"1", "true", "yes", "y"}
CATALOG_DATABASE_FQN = os.environ.get("OPENLAKEFORGE_CATALOG_DATABASE_FQN", f"{CATALOG_SERVICE}.{CATALOG_DATABASE}")
CATALOG_SILVER_SCHEMA_FQNS_RAW = os.environ.get("OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON", "{}")
CATALOG_GOLD_SCHEMA_FQNS_RAW = os.environ.get("OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON", "{}")
STORAGE_SERVICE = os.environ.get("OPENLAKEFORGE_STORAGE_OM_SERVICE", "seaweedfs")
STORAGE_DISPLAY_NAME = os.environ.get("OPENLAKEFORGE_STORAGE_DISPLAY_NAME", "SeaweedFS S3")
STORAGE_ENDPOINT = os.environ.get("OPENLAKEFORGE_STORAGE_ENDPOINT", "http://seaweedfs-s3:8333")
STORAGE_REGION = os.environ.get("OPENLAKEFORGE_STORAGE_REGION", "us-east-1")
STORAGE_BRONZE_BUCKET = os.environ.get(
    "OPENLAKEFORGE_STORAGE_BRONZE_BUCKET",
    os.environ.get("OPENLAKEFORGE_STORAGE_BUCKET", "lakehouse-bronze"),
)
STORAGE_SILVER_BUCKET = os.environ.get("OPENLAKEFORGE_STORAGE_SILVER_BUCKET", "lakehouse-silver")
STORAGE_GOLD_BUCKET = os.environ.get("OPENLAKEFORGE_STORAGE_GOLD_BUCKET", "lakehouse-gold")


class OpenMetadataError(RuntimeError):
    pass


def parse_json_env(name, raw):
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise OpenMetadataError(f"Environment variable {name} must be valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise OpenMetadataError(f"Environment variable {name} must contain a JSON object.")
    return value


CATALOG_SILVER_SCHEMA_FQNS = parse_json_env(
    "OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON",
    CATALOG_SILVER_SCHEMA_FQNS_RAW,
)
CATALOG_GOLD_SCHEMA_FQNS = parse_json_env(
    "OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON",
    CATALOG_GOLD_SCHEMA_FQNS_RAW,
)


def request(method, path, token=None, payload=None, ok_statuses=(200,), content_type="application/json"):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = content_type
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=data,
        method=method,
        headers=headers,
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read().decode("utf-8")
            status = response.status
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", errors="replace")
        status = err.code
    except urllib.error.URLError as err:
        raise OpenMetadataError(f"{method} {path} failed: {err}") from err

    if status not in ok_statuses:
        raise OpenMetadataError(f"{method} {path} failed with HTTP {status}: {body}")

    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def wait_for_openmetadata():
    last_error = None
    for _ in range(120):
        try:
            request("GET", "/api/v1/system/config/jwks")
            return
        except OpenMetadataError as exc:
            last_error = exc
            time.sleep(2)
    raise OpenMetadataError(f"OpenMetadata did not become reachable: {last_error}")


def login():
    encoded_password = base64.b64encode(ADMIN_PASSWORD.encode("utf-8")).decode("ascii")
    response = request(
        "POST",
        "/api/v1/users/login",
        payload={"email": ADMIN_EMAIL, "password": encoded_password},
    )
    token = response.get("accessToken")
    if not token:
        raise OpenMetadataError(f"OpenMetadata login did not return an access token: {response}")
    return token


def load_yaml(path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def display_name_from_name(name):
    return " ".join(part.capitalize() for part in name.replace("-", " ").replace("_", " ").split())


def domain_description(domain):
    parts = []
    if domain.get("description"):
        parts.append(str(domain["description"]))
    if domain.get("status"):
        parts.append(f"Status: {domain['status']}")
    medallion = domain.get("medallion")
    if isinstance(medallion, dict) and medallion:
        parts.append("Medallion layers:")
        for layer, config in medallion.items():
            if isinstance(config, dict):
                owner = config.get("owner", "unknown")
                description = config.get("description", "")
                parts.append(f"- {layer}: {description} Owner: {owner}.")
    return "\n".join(parts)


def domain_payload(domain):
    name = domain.get("name")
    if not name:
        raise OpenMetadataError("OpenMetadata domain metadata is missing required field 'name'.")

    payload = {
        "name": name,
        "displayName": domain.get("displayName") or domain.get("display_name") or display_name_from_name(name),
        "domainType": domain.get("domainType") or domain.get("domain_type") or "Source-aligned",
        "description": domain_description(domain),
    }
    for optional in ["owners", "experts", "reviewers", "tags", "style", "extension"]:
        if domain.get(optional):
            payload[optional] = domain[optional]
    return payload


def product_payload(product):
    name = product.get("name")
    if not name:
        raise OpenMetadataError("OpenMetadata data-product metadata is missing required field 'name'.")

    domains = product.get("domains")
    if domains is None and product.get("domain"):
        domains = [product["domain"]]
    if not domains:
        raise OpenMetadataError(f"OpenMetadata data product '{name}' must define 'domain' or 'domains'.")

    payload = {
        "name": name,
        "displayName": product.get("displayName") or product.get("display_name") or display_name_from_name(name),
        "description": product.get("description") or "",
        "domains": domains,
    }
    for optional in ["owners", "experts", "reviewers", "tags", "style", "extension"]:
        if product.get(optional):
            payload[optional] = product[optional]
    return payload


def domain_files():
    if METADATA_SOURCE_DIR:
        path = Path(METADATA_SOURCE_DIR)
        if not path.exists():
            raise OpenMetadataError(f"OpenMetadata metadata source does not exist: {path}")
        if path.is_file():
            return [path]
        if (path / "domain.yaml").is_file():
            return [path / "domain.yaml"]
        return sorted(path.glob("*/domain.yaml"))

    if not METADATA_ROOT.exists():
        raise OpenMetadataError(f"OpenMetadata metadata root does not exist: {METADATA_ROOT}")

    return sorted(
        path
        for path in METADATA_ROOT.glob("*/domain.yaml")
        if path.is_file()
    )


def product_entries(domain):
    products = domain.get("data_products") or []
    if not isinstance(products, list):
        raise OpenMetadataError(f"Domain '{domain.get('name', '<unknown>')}' data_products must be a list.")
    for product in products:
        if not isinstance(product, dict):
            raise OpenMetadataError(f"Unsupported data product entry in domain '{domain.get('name')}': {product!r}")
        if not product.get("name"):
            product["name"] = f"{domain['name']}_{product.get('id', '')}".rstrip("_")
        if not product.get("domain") and not product.get("domains"):
            product["domain"] = domain["name"]
        yield product


def resolve_table_asset(token, asset):
    if isinstance(asset, str):
        asset_type = "table"
        fqn = asset
    elif isinstance(asset, dict):
        asset_type = asset.get("type", "table")
        fqn = asset.get("fqn") or asset.get("fullyQualifiedName")
    else:
        raise OpenMetadataError(f"Unsupported asset entry: {asset!r}")

    if asset_type != "table":
        raise OpenMetadataError(f"Unsupported OpenMetadata data-product asset type '{asset_type}'. Only 'table' is supported.")
    if not fqn:
        raise OpenMetadataError(f"OpenMetadata data-product asset is missing 'fqn': {asset!r}")

    encoded_fqn = urllib.parse.quote(fqn, safe="")
    try:
        table = request("GET", f"/api/v1/tables/name/{encoded_fqn}?fields=domains", token=token)
    except OpenMetadataError as exc:
        if "HTTP 404" in str(exc):
            return None, fqn
        raise

    table_id = table.get("id")
    if not table_id:
        raise OpenMetadataError(f"OpenMetadata table lookup for '{fqn}' did not return an id: {table}")
    return {
        "id": table_id,
        "type": "table",
        "name": table.get("name"),
        "fullyQualifiedName": table.get("fullyQualifiedName", fqn),
        "displayName": table.get("displayName"),
        "domains": table.get("domains") or [],
    }, None


def resolve_domain_ref(token, domain_name):
    encoded_name = urllib.parse.quote(domain_name, safe="")
    domain = request("GET", f"/api/v1/domains/name/{encoded_name}", token=token)
    domain_id = domain.get("id")
    if not domain_id:
        raise OpenMetadataError(f"OpenMetadata domain lookup for '{domain_name}' did not return an id: {domain}")
    return {
        "id": domain_id,
        "type": "domain",
        "name": domain.get("name", domain_name),
        "fullyQualifiedName": domain.get("fullyQualifiedName", domain_name),
        "displayName": domain.get("displayName"),
    }


def domain_matches(existing, expected):
    return (
        existing.get("id") == expected.get("id")
        or existing.get("fullyQualifiedName") == expected.get("fullyQualifiedName")
        or existing.get("name") == expected.get("name")
    )


def ensure_table_domains(token, table_refs, domain_refs):
    for table_ref in table_refs:
        existing_domains = table_ref.get("domains") or []
        missing_domains = [
            domain_ref
            for domain_ref in domain_refs
            if not any(domain_matches(existing, domain_ref) for existing in existing_domains)
        ]
        if not missing_domains:
            continue

        domains = existing_domains + missing_domains
        request(
            "PATCH",
            f"/api/v1/tables/{table_ref['id']}",
            token=token,
            payload=[{"op": "add", "path": "/domains", "value": domains}],
            content_type="application/json-patch+json",
        )
        table_ref["domains"] = domains
        print(
            "Assigned OpenMetadata domain(s) "
            f"{', '.join(domain['fullyQualifiedName'] for domain in missing_domains)} "
            f"to table: {table_ref['fullyQualifiedName']}"
        )


def data_product_asset_ref(table_ref):
    return {
        "id": table_ref["id"],
        "type": table_ref["type"],
        "name": table_ref.get("name"),
        "fullyQualifiedName": table_ref.get("fullyQualifiedName"),
        "displayName": table_ref.get("displayName"),
    }


def product_contract_key(product):
    return product.get("asset_prefix") or product.get("name") or product.get("id") or ""


def schema_fqn_for_product(product, table_group_key, fallback):
    product_key = product_contract_key(product)
    if table_group_key == "silver_tables":
        return CATALOG_SILVER_SCHEMA_FQNS.get(product_key) or fallback
    if table_group_key == "gold_tables":
        return CATALOG_GOLD_SCHEMA_FQNS.get(product_key) or fallback
    return fallback


def provider_asset_fqn(product, fqn):
    if not fqn:
        return fqn

    table_name = fqn.rsplit(".", 1)[-1]
    for schema_fqn, table in product_table_specs(product):
        if table.get("name") == table_name:
            return f"{schema_fqn}.{table_name}"
    return fqn


def asset_with_provider_fqn(product, asset):
    if isinstance(asset, str):
        return provider_asset_fqn(product, asset)
    if isinstance(asset, dict):
        rewritten = dict(asset)
        fqn = rewritten.get("fqn") or rewritten.get("fullyQualifiedName")
        if fqn:
            rewritten["fqn"] = provider_asset_fqn(product, fqn)
            rewritten.pop("fullyQualifiedName", None)
        return rewritten
    return asset


def product_asset_entries(product):
    seen = set()
    for schema_fqn, table in product_table_specs(product):
        fqn = f"{schema_fqn}.{table['name']}"
        if fqn in seen:
            continue
        seen.add(fqn)
        yield {"type": "table", "fqn": fqn}

    for asset in product.get("assets", []):
        if isinstance(asset, str):
            fqn = asset
        elif isinstance(asset, dict):
            fqn = asset.get("fqn") or asset.get("fullyQualifiedName")
        else:
            fqn = None

        fqn = provider_asset_fqn(product, fqn)
        if fqn and fqn in seen:
            continue
        if fqn:
            seen.add(fqn)
        yield asset_with_provider_fqn(product, asset)


def product_table_specs(product):
    for key in ["silver_tables", "gold_tables"]:
        spec = product.get(key)
        if not spec:
            continue
        schema_fqn = schema_fqn_for_product(product, key, spec.get("schema"))
        if not schema_fqn:
            raise OpenMetadataError(
                f"Data product '{product.get('name')}' table group '{key}' is missing required 'schema'."
            )
        for table in spec.get("tables", []):
            yield schema_fqn, table


def storage_bucket_specs():
    specs = [
        (STORAGE_BRONZE_BUCKET, "Bronze landing bucket for raw immutable source files."),
        (STORAGE_SILVER_BUCKET, "Silver bucket for Floe-validated Iceberg tables and validation reports."),
        (STORAGE_GOLD_BUCKET, "Gold bucket for dbt-owned business marts."),
    ]
    seen = set()
    for name, description in specs:
        if not name or name in seen:
            continue
        seen.add(name)
        yield {
            "name": name,
            "path": f"s3://{name}",
            "description": description,
        }


def validate_bronze_entries(domain_specs):
    for _, domain in domain_specs:
        for product in product_entries(domain):
            for container in product.get("bronze") or []:
                if not container.get("path"):
                    raise OpenMetadataError(f"Data product '{product['name']}' Bronze entry is missing required 'path'.")


def ensure_storage_service(token, name, display_name, endpoint, region):
    aws_config = {"awsRegion": region}
    if endpoint:
        aws_config["endPointURL"] = endpoint
    payload = {
        "name": name,
        "displayName": display_name,
        "serviceType": "S3",
        "connection": {
            "config": {
                "type": "S3",
                "awsConfig": aws_config,
            }
        },
    }
    request("PUT", "/api/v1/services/storageServices", token=token, payload=payload, ok_statuses=(200, 201))
    print(f"Upserted OpenMetadata storage service: {name}")


def ensure_container(token, service, name, parent_fqn, full_path, description):
    payload = {
        "name": name,
        "service": service,
        "fullPath": full_path,
        "description": description,
    }
    if parent_fqn:
        encoded = urllib.parse.quote(parent_fqn, safe="")
        parent = request("GET", f"/api/v1/containers/name/{encoded}", token=token)
        parent_id = parent.get("id")
        if not parent_id:
            raise OpenMetadataError(f"OpenMetadata container lookup for '{parent_fqn}' did not return an id: {parent}")
        payload["parent"] = {"id": parent_id, "type": "container"}
    request("PUT", "/api/v1/containers", token=token, payload=payload, ok_statuses=(200, 201))
    print(f"Upserted OpenMetadata container: {full_path}")


def ensure_table_stub(token, schema_fqn, name, description):
    payload = {
        "name": name,
        "databaseSchema": schema_fqn,
        "columns": [],
    }
    if description:
        payload["description"] = description
    request("PUT", "/api/v1/tables", token=token, payload=payload, ok_statuses=(200, 201))
    print(f"Upserted OpenMetadata table stub: {schema_fqn}.{name}")


def cleanup_legacy_default_database(token):
    if not CLEANUP_LEGACY_DEFAULT_DATABASE or CATALOG_DATABASE == "default":
        return

    target_fqn = CATALOG_DATABASE_FQN
    legacy_fqn = f"{CATALOG_SERVICE}.default"
    encoded_target = urllib.parse.quote(target_fqn, safe="")
    encoded_legacy = urllib.parse.quote(legacy_fqn, safe="")

    try:
        request("GET", f"/api/v1/databases/name/{encoded_target}", token=token)
    except OpenMetadataError as exc:
        if "HTTP 404" in str(exc):
            print(
                f"WARN: Skipping legacy OpenMetadata database cleanup because "
                f"target database is missing: {target_fqn}",
                file=sys.stderr,
            )
            return
        raise

    try:
        legacy = request("GET", f"/api/v1/databases/name/{encoded_legacy}", token=token)
    except OpenMetadataError as exc:
        if "HTTP 404" in str(exc):
            return
        raise

    legacy_id = legacy.get("id")
    if not legacy_id:
        raise OpenMetadataError(f"OpenMetadata database lookup for '{legacy_fqn}' did not return an id: {legacy}")

    request(
        "DELETE",
        f"/api/v1/databases/{legacy_id}?recursive=true&hardDelete=true",
        token=token,
        ok_statuses=(200, 202, 204),
    )
    print(f"Deleted legacy OpenMetadata database metadata: {legacy_fqn}")


def deploy():
    wait_for_openmetadata()
    token = login()

    domain_specs = [(path, load_yaml(path)) for path in domain_files()]
    if not domain_specs:
        raise OpenMetadataError(
            f"No OpenMetadata domain metadata files found under {METADATA_ROOT}/<domain>/domain.yaml"
        )

    # Phase A+B: Object Store service and medallion bucket containers.
    # The seeding gives browse-level visibility in OM's Storage section immediately.
    # Lineage integration is intentionally deferred; see ADR 0009.
    ensure_storage_service(token, STORAGE_SERVICE, STORAGE_DISPLAY_NAME, STORAGE_ENDPOINT, STORAGE_REGION)
    validate_bronze_entries(domain_specs)
    for container in storage_bucket_specs():
        ensure_container(
            token,
            STORAGE_SERVICE,
            container["name"],
            None,
            container["path"],
            container["description"],
        )

    # Phase C: Pre-seed Iceberg table stubs so governed assets are visible before
    # the hourly Polaris crawler refreshes table metadata.
    for _, domain in domain_specs:
        for product in product_entries(domain):
            for schema_fqn, table in product_table_specs(product):
                ensure_table_stub(token, schema_fqn, table["name"], table.get("description", ""))
    cleanup_legacy_default_database(token)

    # Phase D: Upsert domains and data products from governance YAML.
    missing_assets = []
    for _, domain in domain_specs:
        domain_body = domain_payload(domain)
        request("PUT", "/api/v1/domains", token=token, payload=domain_body, ok_statuses=(200, 201))
        print(f"Upserted OpenMetadata domain: {domain_body['name']}")

        for product in product_entries(domain):
            product_body = product_payload(product)
            request("PUT", "/api/v1/dataProducts", token=token, payload=product_body, ok_statuses=(200, 201))
            print(f"Upserted OpenMetadata data product: {product_body['name']}")
            domain_refs = [resolve_domain_ref(token, domain_name) for domain_name in product_body["domains"]]

            refs = []
            for asset in product_asset_entries(product):
                ref, missing_fqn = resolve_table_asset(token, asset)
                if missing_fqn:
                    missing_assets.append(missing_fqn)
                else:
                    refs.append(ref)

            if refs:
                ensure_table_domains(token, refs, domain_refs)
                product_name = urllib.parse.quote(product_body["name"], safe="")
                request(
                    "PUT",
                    f"/api/v1/dataProducts/{product_name}/assets/add",
                    token=token,
                    payload={"assets": [data_product_asset_ref(ref) for ref in refs], "dryRun": False},
                    ok_statuses=(200, 201),
                )
                print(f"Attached {len(refs)} OpenMetadata asset(s) to data product: {product_body['name']}")

    if missing_assets:
        message = "\n".join(f"  - {fqn}" for fqn in sorted(set(missing_assets)))
        guidance = (
            "OpenMetadata table assets are not available yet:\n"
            f"{message}\n"
            "Run the product ETL jobs in Dagster, wait for the catalog metadata ingestion to crawl the catalog, "
            "then rerun 'make openmetadata-metadata-deploy'."
        )
        if ALLOW_MISSING_ASSETS:
            print(f"WARN: {guidance}", file=sys.stderr)
        else:
            raise OpenMetadataError(guidance)


try:
    deploy()
except OpenMetadataError as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    raise SystemExit(1)
PY

echo "Deployed OpenMetadata governance metadata."
