"""Optional governance/analytics platform layer gating."""

from __future__ import annotations

from olf.e2e._shell import E2EConfig, E2EError, Layer, load_provider_contracts_or_raise


def configured_layers(cfg: E2EConfig) -> dict[Layer, bool]:
    """Read enabled platform layers once from the environment contract."""
    provider_contracts = load_provider_contracts_or_raise(cfg)
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
