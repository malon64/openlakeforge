"""Optional governance/analytics platform layer gating."""

from __future__ import annotations

from olf.e2e._shell import E2EConfig, E2EError, Layer, load_provider_contracts_or_raise


def configured_layers(cfg: E2EConfig) -> dict[Layer, bool]:
    """Read enabled platform layers once from the environment contract.

    Every current root emits a v3 (`schema_version` "3.0.0") contract: the
    stage-aware family nests `reporting`/`governance` inside each stage's own
    block (aws-poc/local/azure contracts.tf), present only when that stage's
    analytics/governance capability is on - there is no top-level "enabled"
    field to read, key absence *is* the disabled signal. Falls back to the
    legacy v2 shape's top-level `reporting`/`governance` blocks (each with an
    explicit `enabled` bool) for any contract still on that schema.
    """
    provider_contracts = load_provider_contracts_or_raise(cfg)
    stages = provider_contracts.get("stages")
    if isinstance(stages, dict) and stages:
        stage = provider_contracts["stages"][next(iter(stages))]
        return {
            "governance": "governance" in stage,
            "analytics": "reporting" in stage,
        }
    layers = {
        "governance": provider_contracts.get("governance") or {},
        "analytics": provider_contracts.get("reporting") or {},
    }
    enabled: dict[Layer, bool] = {}
    for layer, contract in layers.items():
        value = contract.get("enabled", True)
        if not isinstance(value, bool):
            raise E2EError(f"provider_contracts.{layer}.enabled must be a boolean.")
        enabled[layer] = value
    return enabled
