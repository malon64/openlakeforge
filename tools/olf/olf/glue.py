"""AWS Glue client for namespace reconciliation over boto3.

See `olf.catalog` for why this exists and the provider-neutral planner it
implements: `plan_namespace_sync` / `apply_namespace_sync` treat this client
as a `NamespaceClient` and do not otherwise know they are talking to Glue.

A Glue "namespace" is a database. Unlike Polaris, `DeleteDatabase` cascades to
every table inside rather than refusing -- Glue has no equivalent of Polaris's
409-on-non-empty-namespace response. `drop_namespace` below checks
`GetTables` itself before deleting, so a `--prune` run cannot silently take
data with it here either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import boto3

from olf.inventory import CatalogNamespace

LOCATION_PROPERTY = "LocationUri"


class GlueError(RuntimeError):
    """Raised when Glue rejects a request."""


@dataclass(frozen=True)
class GlueConfig:
    """Everything needed to reach one AWS Glue Data Catalog."""

    catalog_id: str
    region: str


@dataclass
class GlueClient:
    """Minimal Glue Data Catalog client for namespace reconciliation."""

    config: GlueConfig
    _client: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self._client is None:
            self._client = boto3.client("glue", region_name=self.config.region or None)

    def list_namespaces(self) -> dict[str, str]:
        """Return every database in this catalog mapped to its LocationUri."""
        namespaces: dict[str, str] = {}
        paginator = self._client.get_paginator("get_databases")
        for page in paginator.paginate(CatalogId=self.config.catalog_id):
            for database in page.get("DatabaseList", []):
                namespaces[database["Name"]] = database.get(LOCATION_PROPERTY, "")
        return namespaces

    def _database_input(self, namespace: CatalogNamespace) -> dict:
        return {
            "Name": namespace.name,
            LOCATION_PROPERTY: namespace.location,
            "Description": f"OpenLakeForge {self.config.catalog_id} {namespace.name} Iceberg namespace",
        }

    def create_namespace(self, namespace: CatalogNamespace) -> None:
        try:
            self._client.create_database(
                CatalogId=self.config.catalog_id, DatabaseInput=self._database_input(namespace)
            )
        except self._client.exceptions.AlreadyExistsException:
            pass

    def update_namespace_location(self, namespace: CatalogNamespace) -> None:
        self._client.update_database(
            CatalogId=self.config.catalog_id, Name=namespace.name, DatabaseInput=self._database_input(namespace)
        )

    def drop_namespace(self, name: str) -> None:
        """Drop an empty database.

        Glue's DeleteDatabase cascades to every table inside rather than
        refusing, so this checks GetTables first -- the same backstop
        `PolarisClient.drop_namespace` gets for free from Polaris's 409.
        """
        tables = self._client.get_tables(CatalogId=self.config.catalog_id, DatabaseName=name, MaxResults=1)
        table_names = [table["Name"] for table in tables.get("TableList", [])]
        if table_names:
            raise GlueError(
                f"Glue refused to drop database {name!r}: it still holds tables "
                f"(e.g. {table_names[0]!r}). Drop or move its tables first, then rerun with --prune."
            )
        try:
            self._client.delete_database(CatalogId=self.config.catalog_id, Name=name)
        except self._client.exceptions.EntityNotFoundException:
            pass
