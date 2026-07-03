"""dbt-duckdb plugin that works around a DuckDB Iceberg + AWS Glue write gap.

Background
----------
DuckDB's ``iceberg`` extension (>= 1.4.x) can read Glue Iceberg tables through the
AWS Glue Iceberg REST catalog, but its ``createTable`` request omits the table
``location``. Apache Polaris auto-assigns a location from the namespace, so writes
work locally; AWS Glue refuses and returns::

    400 InvalidInputException: Location information cannot be null while creating an iceberg table

There is no DuckDB ATTACH option or SQL clause to supply the location (see the
upstream duckdb-iceberg issue referenced in docs/technical-debt.md).

Workaround
----------
This plugin registers a DuckDB scalar UDF, ``olf_glue_ensure_iceberg_table``, that
creates the target Glue Iceberg table *with* a location by calling the Glue Iceberg
REST catalog directly (SigV4-signed via botocore, which is already in the image; no
pyiceberg needed). The ``iceberg_table`` materialization stages the model result in
a local DuckDB table, calls this UDF to (re)create the Glue table with the staged
schema, then loads it with a plain ``INSERT`` (which DuckDB performs correctly).

The UDF derives the table location from the namespace ``location`` property that the
Glue REST catalog already exposes, so it stays in sync with the Terraform-provisioned
Glue database ``LocationUri``. It drops-and-recreates on every run to match the
full-refresh semantics of the ``iceberg_table`` materialization.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import botocore.session
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from dbt.adapters.duckdb.plugins import BasePlugin

try:  # duckdb.typing is deprecated in favor of duckdb.sqltypes in newer DuckDB
    from duckdb.sqltypes import VARCHAR
except ImportError:  # pragma: no cover - older DuckDB
    from duckdb.typing import VARCHAR

_UDF_NAME = "olf_glue_ensure_iceberg_table"
_SERVICE = "glue"


def _region() -> str:
    return (
        os.environ.get("OPENLAKEFORGE_CATALOG_GLUE_REGION")
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "eu-west-1"
    )


def _rest_base() -> str:
    account = os.environ.get("OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID")
    if not account:
        raise RuntimeError("OPENLAKEFORGE_CATALOG_GLUE_CATALOG_ID is not set")
    endpoint = (
        os.environ.get("OPENLAKEFORGE_CATALOG_GLUE_REST_URI")
        or f"https://{_SERVICE}.{_region()}.amazonaws.com/iceberg"
    ).rstrip("/")
    # Glue REST addresses catalogs as an escaped path segment: catalogs%2F<account>.
    return f"{endpoint}/v1/catalogs%2F{account}"


def _iceberg_type(duckdb_type: str) -> str:
    """Map a DuckDB column type (as emitted by DESCRIBE) to an Iceberg type."""
    t = duckdb_type.strip().upper()
    if t.startswith("DECIMAL"):
        # DECIMAL(18, 3) -> decimal(18,3)
        return "decimal" + t[len("DECIMAL"):].replace(" ", "")
    simple = {
        "BOOLEAN": "boolean",
        "BOOL": "boolean",
        "TINYINT": "int",
        "SMALLINT": "int",
        "INTEGER": "int",
        "INT": "int",
        "BIGINT": "long",
        "HUGEINT": "long",
        "FLOAT": "float",
        "REAL": "float",
        "DOUBLE": "double",
        "VARCHAR": "string",
        "TEXT": "string",
        "STRING": "string",
        "DATE": "date",
        "TIME": "time",
        "TIMESTAMP": "timestamp",
        "DATETIME": "timestamp",
        "TIMESTAMP WITH TIME ZONE": "timestamptz",
        "TIMESTAMPTZ": "timestamptz",
        "BLOB": "binary",
        "BYTEA": "binary",
        "UUID": "uuid",
    }
    if t in simple:
        return simple[t]
    raise RuntimeError(f"Unsupported DuckDB type for Glue Iceberg mapping: {duckdb_type!r}")


def _call(method: str, url: str, body: dict | None = None) -> tuple[int, bytes]:
    creds = botocore.session.get_session().get_credentials().get_frozen_credentials()
    data = json.dumps(body).encode() if body is not None else None
    signed = AWSRequest(
        method=method,
        url=url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
    )
    SigV4Auth(creds, _SERVICE, _region()).add_auth(signed)
    request = urllib.request.Request(url, data=data, headers=dict(signed.headers), method=method)
    try:
        resp = urllib.request.urlopen(request)
        return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _ensure_iceberg_table(namespace: str, table: str, columns_json: str) -> str:
    """Drop-and-recreate ``namespace.table`` in Glue with an explicit location.

    Returns the resolved table location. Raises on any non-success Glue response so
    the failure surfaces in the dbt run instead of silently producing an empty mart.
    """
    base = _rest_base()

    # 1. Resolve the namespace location the Glue REST catalog exposes.
    status, payload = _call("GET", f"{base}/namespaces/{namespace}")
    if status != 200:
        raise RuntimeError(
            f"Glue loadNamespace {namespace} failed ({status}): {payload[:300]!r}"
        )
    ns_location = (json.loads(payload).get("properties") or {}).get("location")
    if not ns_location:
        raise RuntimeError(
            f"Glue namespace {namespace} has no 'location' property; cannot place table {table}"
        )
    table_location = ns_location.rstrip("/") + "/" + table

    # 2. Build the Iceberg schema from the staged DuckDB columns.
    columns = json.loads(columns_json)
    fields = [
        {"id": i, "name": col["name"], "required": False, "type": _iceberg_type(col["type"])}
        for i, col in enumerate(columns, start=1)
    ]
    schema = {
        "type": "struct",
        "schema-id": 0,
        "identifier-field-ids": [],
        "fields": fields,
    }

    # 3. Full-refresh: drop if present (ignore 404), then create with the location.
    _call("DELETE", f"{base}/namespaces/{namespace}/tables/{table}")
    status, payload = _call(
        "POST",
        f"{base}/namespaces/{namespace}/tables",
        {
            "name": table,
            "location": table_location,
            "schema": schema,
            "stage-create": False,
            "properties": {"olf.managed": "true"},
        },
    )
    if status not in (200, 201):
        raise RuntimeError(
            f"Glue createTable {namespace}.{table} failed ({status}): {payload[:400]!r}"
        )
    return table_location


class Plugin(BasePlugin):
    """Registers the ``olf_glue_ensure_iceberg_table`` UDF on the DuckDB connection."""

    def _register(self, conn) -> None:
        # dbt-duckdb configures both the parent connection and every per-model cursor
        # copy. Cursors share the parent's catalog, so the UDF registered on the
        # connection is already visible on the cursor; registering again raises
        # "already exists". Treat that as success so registration is idempotent across
        # the connection and all cursor copies.
        try:
            conn.create_function(
                _UDF_NAME,
                _ensure_iceberg_table,
                [VARCHAR, VARCHAR, VARCHAR],
                VARCHAR,
            )
        except Exception as exc:  # noqa: BLE001
            if "already exists" in str(exc).lower():
                return
            raise

    def configure_connection(self, conn) -> None:
        self._register(conn)

    def configure_cursor(self, cursor) -> None:
        self._register(cursor)
