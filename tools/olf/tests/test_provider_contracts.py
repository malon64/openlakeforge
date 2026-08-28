from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from olf.contracts import build_contract_env
from olf.profile import resolve_topology, validate_deployment_profile
from olf.provider_contracts import ProviderContractError, aws_catalog_name, parse_provider_contracts

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA = json.loads((REPO_ROOT / "docs/schema/provider-contracts.schema.json").read_text())


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _topology(contract: dict):
    stages = {
        name: {
            "enabled": True,
            "capabilities": {
                "analytics": "reporting" in stage,
                "governance": "governance" in stage,
            },
        }
        for name, stage in contract["stages"].items()
    }
    deployment = contract["deployment"]
    return resolve_topology(
        validate_deployment_profile(
            {
                "apiVersion": "openlakeforge.io/v1alpha1",
                "kind": "DeploymentProfile",
                "metadata": {"name": deployment["profile_name"]},
                "spec": {
                    "provider": {
                        "type": deployment["provider"],
                        **({"region": deployment["region"]} if deployment["region"] else {}),
                    },
                    "preset": "slim",
                    "stages": stages,
                },
            }
        )
    )


@pytest.mark.parametrize(
    "fixture_name",
    ["local-provider-contracts-v3.json", "azure-provider-contracts-v3.json", "aws-provider-contracts-v3.json"],
)
def test_v3_provider_fixtures_match_the_strict_schema_and_topology(fixture_name: str) -> None:
    contract = _fixture(fixture_name)

    jsonschema.validate(contract, SCHEMA)
    parsed = parse_provider_contracts(contract, _topology(contract))

    assert {stage.value for stage in parsed.stages} == set(contract["stages"])


def test_aws_fixture_preserves_logical_names_with_distinct_glue_catalogs() -> None:
    contract = _fixture("aws-provider-contracts-v3.json")
    parsed = parse_provider_contracts(contract, _topology(contract))

    dev = parsed.for_stage("dev")
    prod = parsed.for_stage("prod")

    assert dev.catalog["catalog_name"] == "lakehouse_dev"
    assert prod.catalog["catalog_name"] == "lakehouse_prod"
    assert dev.catalog["glue_catalog_id"] == "123456789012:olf_acme_data_dev"
    assert prod.catalog["glue_catalog_id"] == "123456789012:olf_acme_data_prod"
    assert dev.catalog["glue_catalog_id"] != prod.catalog["glue_catalog_id"]
    assert dev.catalog["catalog_namespace_model"] == prod.catalog["catalog_namespace_model"] == "medallion-owner"


def test_aws_catalog_name_is_stable_and_obeys_the_glue_limit() -> None:
    profile = "a" * 80
    name = aws_catalog_name(profile, "prod")

    assert len(name) == 64
    assert name.startswith("olf_")
    assert name.endswith("_prod_" + name[-8:])


def test_native_v3_requires_explicit_stage_for_environment_generation() -> None:
    contract = _fixture("local-provider-contracts-v3.json")

    with pytest.raises(ProviderContractError, match="requires an explicit stage"):
        build_contract_env({}, contract, repo_root=REPO_ROOT, topology=_topology(contract))


def test_stage_environment_exposes_only_its_selected_stage_values() -> None:
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)

    dev_exports, _ = build_contract_env({}, contract, repo_root=REPO_ROOT, topology=topology, stage="dev")
    prod_exports, _ = build_contract_env({}, contract, repo_root=REPO_ROOT, topology=topology, stage="prod")

    assert dev_exports["OPENLAKEFORGE_STORAGE_BRONZE_BUCKET"] == "acme-dev-bronze"
    assert dev_exports["OPENLAKEFORGE_CATALOG_NAME"] == "lakehouse_dev"
    assert dev_exports["OPENLAKEFORGE_ARTIFACT_BASE_URI"].endswith("activations/dev")
    assert "prod" not in "\n".join(dev_exports.values())
    assert prod_exports["OPENLAKEFORGE_STORAGE_BRONZE_BUCKET"] == "acme-prod-bronze"
    assert prod_exports["OPENLAKEFORGE_CATALOG_NAME"] == "lakehouse_prod"
    assert "dev" not in "\n".join(prod_exports.values())


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda contract: contract.__setitem__("schema_version", "9.0.0"), "unsupported"),
        (lambda contract: contract["stages"].pop("prod"), "must equal enabled topology stages"),
        (
            lambda contract: contract["stages"].__setitem__("uat", copy.deepcopy(contract["stages"]["dev"])),
            "must equal enabled topology stages",
        ),
        (
            lambda contract: contract["stages"]["dev"]["query"].__setitem__("service_ref", "shared/missing"),
            "does not resolve",
        ),
        (
            lambda contract: contract["stages"]["dev"]["query"].__setitem__("catalog_ref", "stage/prod/catalog"),
            "cannot reference another stage",
        ),
        (
            lambda contract: contract["stages"]["prod"]["storage"]["gold"].__setitem__("physical_id", "acme-dev-gold"),
            "shared between stages",
        ),
        (lambda contract: contract["stages"]["dev"].__setitem__("unexpected", True), "unsupported fields"),
    ],
)
def test_v3_contracts_fail_closed_for_cross_stage_and_unknown_shapes(mutate, match: str) -> None:
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    mutate(contract)

    with pytest.raises(ProviderContractError, match=match):
        parse_provider_contracts(contract, topology)


def test_v3_contract_rejects_a_missing_capability_binding() -> None:
    contract = _fixture("aws-provider-contracts-v3.json")
    topology = _topology(contract)
    del contract["stages"]["dev"]["reporting"]

    with pytest.raises(ProviderContractError, match="reporting must match"):
        parse_provider_contracts(contract, topology)


def test_v2_contract_lifts_to_legacy_dev_without_changing_environment_output() -> None:
    contract = _fixture("local-provider-contracts.json")
    parsed = parse_provider_contracts(contract)
    exports, _ = build_contract_env({}, contract, repo_root=REPO_ROOT)

    assert parsed.compatibility_v2 is True
    assert parsed.for_stage().name.value == "dev"
    assert exports["OPENLAKEFORGE_CATALOG_NAME"] == "lakehouse_dev"
