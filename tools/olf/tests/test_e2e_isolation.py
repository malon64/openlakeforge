from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from conftest import e2e_cfg

from olf.e2e import _isolation
from olf.e2e._shell import E2EError


class _S3Client:
    def __init__(self) -> None:
        self.head_buckets: list[str] = []
        self.prefixes: list[str] = []

    def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803
        self.head_buckets.append(Bucket)
        if Bucket == "openlakeforge-prod-bronze":
            raise ClientError({"Error": {"Code": "403", "Message": "denied"}}, "HeadBucket")

    def list_objects_v2(self, *, Bucket: str, Prefix: str, MaxKeys: int) -> None:  # noqa: N803, ARG002
        self.prefixes.append(Prefix)
        if Prefix == "activations/prod/":
            raise ClientError({"Error": {"Code": "403", "Message": "denied"}}, "ListObjectsV2")


def test_isolation_uses_the_selected_stage_bucket_from_the_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cfg = replace(e2e_cfg(tmp_path), namespace="olf-dev", shared_namespace="olf-system")
    provider_contracts = {
        "stages": {
            "dev": {"storage": {"bronze": {"bucket_name": "lakehouse-bronze"}}},
            "prod": {"storage": {"bronze": {"bucket_name": "openlakeforge-prod-bronze"}}},
        }
    }
    client = _S3Client()

    monkeypatch.setattr(_isolation, "load_provider_contracts_or_raise", lambda _cfg: provider_contracts)
    monkeypatch.setattr(_isolation.k8s, "resource_exists", lambda *_args: True)
    def _trino_query(_cfg, sql: str) -> str:  # noqa: ANN001
        if "lakehouse_prod" in sql:
            raise E2EError("denied")
        return ""

    monkeypatch.setattr(_isolation, "trino_query", _trino_query)
    monkeypatch.setattr(_isolation, "_s3_identity", lambda *_args: ("key", "secret"))
    monkeypatch.setattr(_isolation, "_s3_client", lambda *_args, **_kwargs: client)

    @contextmanager
    def _port_forward(*_args, **_kwargs):  # noqa: ANN202
        yield 8333

    monkeypatch.setattr(_isolation.k8s, "port_forward", _port_forward)

    _isolation.check_stage_isolation(cfg)

    assert client.head_buckets == ["openlakeforge-prod-bronze", "lakehouse-bronze"]
    assert client.prefixes == ["activations/prod/", "activations/dev/"]
