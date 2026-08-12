from __future__ import annotations

import json

import pytest

from olf import polaris
from olf.catalog import NamespaceState


class FakeResponse:
    def __init__(self, body: dict, status: int = 200) -> None:
        self._body = json.dumps(body).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_list_namespaces_skips_nested_namespaces(monkeypatch: pytest.MonkeyPatch) -> None:
    config = polaris.PolarisConfig(
        base_url="http://127.0.0.1:8181",
        catalog_name="lakehouse_dev",
        client_id="id",
        client_secret="secret",
    )
    client = polaris.PolarisClient(config, token="t")

    def fake_urlopen(request, timeout=0):  # noqa: ANN001, ARG001 - urllib signature
        if request.full_url.endswith("/namespaces"):
            return FakeResponse({"namespaces": [["sales_silver"], ["sales", "nested"]]})
        return FakeResponse({"properties": {"location": "s3://silver/sales_silver/"}})

    monkeypatch.setattr(polaris.urllib.request, "urlopen", fake_urlopen)

    assert client.list_namespaces() == {
        "sales_silver": NamespaceState("s3://silver/sales_silver/", {"location": "s3://silver/sales_silver/"})
    }
