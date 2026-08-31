from pathlib import Path

import pytest
from conftest import e2e_cfg

from olf.e2e import _layers


def test_configured_layers_reads_contract_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        _layers,
        "load_provider_contracts_or_raise",
        lambda _cfg: {"governance": {"enabled": False}, "reporting": {"enabled": False}},
    )

    assert _layers.configured_layers(e2e_cfg(tmp_path)) == {"governance": False, "analytics": False}


def test_configured_layers_reads_v3_stage_nested_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """v3 contracts nest reporting/governance per stage, present only when
    that stage's analytics/governance capability is on (contracts.tf) - key
    absence is the disabled signal, there is no "enabled" field to read."""
    monkeypatch.setattr(
        _layers,
        "load_provider_contracts_or_raise",
        lambda _cfg: {
            "schema_version": "3.0.0",
            "stages": {"dev": {"reporting": {"service_ref": "stage/dev/reporting"}}},
        },
    )

    assert _layers.configured_layers(e2e_cfg(tmp_path)) == {"governance": False, "analytics": True}
