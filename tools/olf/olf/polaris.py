"""Polaris client for namespace reconciliation over the Iceberg REST API.

See `olf.catalog` for why this exists and the provider-neutral planner it
implements: `plan_namespace_sync` / `apply_namespace_sync` treat this client
as a `NamespaceClient` and do not otherwise know they are talking to Polaris.
The REST calls below are the ones the old Phase 1 bootstrap Job made with
curl; only the host moved.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from olf.inventory import CatalogNamespace

LOCATION_PROPERTY = "location"


class PolarisError(RuntimeError):
    """Raised when Polaris rejects a request or is unreachable."""


@dataclass(frozen=True)
class PolarisConfig:
    """Everything needed to reach one Polaris catalog as the deployer principal."""

    base_url: str
    catalog_name: str
    client_id: str
    client_secret: str
    oauth_scope: str = "PRINCIPAL_ROLE:ALL"

    @property
    def token_path(self) -> str:
        return "/api/catalog/v1/oauth/tokens"

    @property
    def catalog_path(self) -> str:
        return f"/api/catalog/v1/{urllib.parse.quote(self.catalog_name, safe='')}"


@dataclass
class PolarisClient:
    """Minimal Iceberg REST catalog client for namespace reconciliation."""

    config: PolarisConfig
    token: str | None = field(default=None)

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        ok_statuses: tuple[int, ...] = (200,),
    ) -> dict:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        request = urllib.request.Request(
            f"{self.config.base_url}{path}", data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - localhost forward
                body = response.read().decode("utf-8")
                status = response.status
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", errors="replace")
            status = err.code
        except urllib.error.URLError as err:
            raise PolarisError(f"{method} {path} failed: {err}") from err

        if status not in ok_statuses:
            raise PolarisError(f"{method} {path} failed with HTTP {status}: {body}")
        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body}

    def login(self) -> None:
        """Exchange the deployer client credentials for a bearer token."""
        form = urllib.parse.urlencode(
            {"grant_type": "client_credentials", "scope": self.config.oauth_scope}
        ).encode("utf-8")
        basic = base64.b64encode(
            f"{self.config.client_id}:{self.config.client_secret}".encode()
        ).decode("ascii")
        request = urllib.request.Request(
            f"{self.config.base_url}{self.config.token_path}",
            data=form,
            method="POST",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - localhost forward
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as err:
            detail = err.read().decode("utf-8", errors="replace")
            raise PolarisError(f"Polaris token request failed with HTTP {err.code}: {detail}") from err
        except urllib.error.URLError as err:
            raise PolarisError(f"Polaris token request failed: {err}") from err

        token = body.get("access_token")
        if not token:
            raise PolarisError("Polaris token response contained no access_token")
        self.token = token

    def _namespace_path(self, name: str) -> str:
        return f"{self.config.catalog_path}/namespaces/{urllib.parse.quote(name, safe='')}"

    def list_namespaces(self) -> dict[str, str]:
        """Return every top-level namespace mapped to its `location` property.

        Multi-level namespaces are not part of this model -- Glue has no nesting
        and Trino's Iceberg connector flattens to a single schema level -- so a
        nested namespace is a foreign object this command must not touch.
        """
        response = self._request("GET", f"{self.config.catalog_path}/namespaces")
        names = [levels[0] for levels in response.get("namespaces", []) if len(levels) == 1]
        return {name: self.namespace_location(name) for name in names}

    def namespace_location(self, name: str) -> str:
        response = self._request("GET", self._namespace_path(name))
        properties = response.get("properties") or {}
        return properties.get(LOCATION_PROPERTY, "")

    def create_namespace(self, namespace: CatalogNamespace) -> None:
        self._request(
            "POST",
            f"{self.config.catalog_path}/namespaces",
            payload={
                "namespace": [namespace.name],
                "properties": {LOCATION_PROPERTY: namespace.location},
            },
            ok_statuses=(200, 409),
        )

    def update_namespace_location(self, namespace: CatalogNamespace) -> None:
        self._request(
            "POST",
            f"{self._namespace_path(namespace.name)}/properties",
            payload={"removals": [], "updates": {LOCATION_PROPERTY: namespace.location}},
            ok_statuses=(200,),
        )

    def drop_namespace(self, name: str) -> None:
        """Drop an empty namespace.

        Polaris answers 409 while the namespace still holds tables. That is the
        backstop against a prune quietly taking data with it, so it is reported
        rather than swallowed.
        """
        try:
            self._request("DELETE", self._namespace_path(name), ok_statuses=(200, 204, 404))
        except PolarisError as err:
            raise PolarisError(
                f"Polaris refused to drop namespace {name!r}: {err}. "
                "Drop or move its tables first, then rerun with --prune."
            ) from err
