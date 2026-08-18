"""Superset REST client for e2e dashboard validation."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from .base import ServiceClientError


class SupersetClient:
    """Superset REST API client for dashboard queries."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def dashboards(self) -> list[Mapping[str, Any]]:
        """List all dashboards via the REST API."""
        token = self._login()
        import requests

        response = requests.get(
            f"{self.base_url}/api/v1/dashboard/",
            params={"q": '{"page_size": 100}'},
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json().get("result", [])

    def _login(self) -> str:
        """Log in and return bearer token."""
        import requests

        last_error: Exception | None = None
        for _ in range(60):
            try:
                response = requests.post(
                    f"{self.base_url}/api/v1/security/login",
                    json={"username": "admin", "password": "admin", "provider": "db", "refresh": True},
                    headers={"Accept": "application/json"},
                    timeout=30,
                )
                response.raise_for_status()
                return str(response.json()["access_token"])
            except Exception as exc:
                last_error = exc
                time.sleep(2)
        raise ServiceClientError(f"Superset login failed: {last_error}")
