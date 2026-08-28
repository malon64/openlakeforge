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


def test_duplicate_bucket_name_across_stages_is_rejected() -> None:
    """A wiring mistake could give two stages distinct physical_id values but
    the same bucket_name; as_v2_environment_contract() exports bucket_name,
    so this must fail closed even though physical_id is already unique."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["prod"]["storage"]["bronze"]["bucket_name"] = "acme-dev-bronze"

    with pytest.raises(ProviderContractError, match="shared between stages"):
        parse_provider_contracts(contract, topology)


def test_dev_reporting_cannot_reference_prod() -> None:
    contract = _fixture("aws-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["reporting"]["service_ref"] = "stage/prod/reporting"

    with pytest.raises(ProviderContractError, match="must reference stage/dev"):
        parse_provider_contracts(contract, topology)


def test_dev_orchestration_endpoint_cannot_reference_prod() -> None:
    contract = _fixture("aws-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["orchestration"]["endpoint_ref"] = "stage/prod/endpoints/orchestration"
    contract["stages"]["dev"]["endpoints"]["orchestration"] = "stage/prod/endpoints/orchestration"

    with pytest.raises(ProviderContractError, match="must reference one of stage/dev"):
        parse_provider_contracts(contract, topology)


def test_dev_endpoints_catalog_cannot_reference_prod() -> None:
    contract = _fixture("aws-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["endpoints"]["catalog"] = "stage/prod/catalog"

    with pytest.raises(ProviderContractError, match="must resolve the stage catalog"):
        parse_provider_contracts(contract, topology)


@pytest.mark.parametrize(
    ("catalog_type", "catalog_provider", "match"),
    [
        ("nonsense", "polaris", "catalog_type must be one of"),
        ("rest", "polrais", "catalog_provider must be one of"),
        ("glue", "polaris", "does not match"),
        ("rest", "aws-glue", "does not match"),
    ],
)
def test_catalog_type_and_provider_must_be_a_known_matching_pair(
    catalog_type: str, catalog_provider: str, match: str
) -> None:
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["catalog"]["catalog_type"] = catalog_type
    contract["stages"]["dev"]["catalog"]["catalog_provider"] = catalog_provider

    with pytest.raises(ProviderContractError, match=match):
        parse_provider_contracts(contract, topology)


def test_stage_storage_region_must_match_deployment_region() -> None:
    contract = _fixture("aws-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["storage"]["region"] = "us-east-1"

    with pytest.raises(ProviderContractError, match="storage.region must match"):
        parse_provider_contracts(contract, topology)


def test_glue_region_must_match_deployment_region() -> None:
    contract = _fixture("aws-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["catalog"]["glue_region"] = "ap-south-1"

    with pytest.raises(ProviderContractError, match="glue_region must match"):
        parse_provider_contracts(contract, topology)


def test_local_provider_region_is_unconstrained() -> None:
    """Local deployments have no cloud region (deployment.region is null);
    the S3-compatible region string is a placeholder and must not be forced
    to agree with a region the deployment does not have."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    assert topology.region is None

    parse_provider_contracts(contract, topology)  # does not raise


def test_prod_stage_does_not_leak_dev_polaris_warehouse() -> None:
    """Regression for the two-pass default/apply ordering in build_contract_env:
    the first default pass pins POLARIS_WAREHOUSE to the dev default before
    contracts apply, and env.default() will not move an already-set value, so
    a PROD stage must be re-derived explicitly rather than keeping that stale
    default."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)

    prod_exports, _ = build_contract_env({}, contract, repo_root=REPO_ROOT, topology=topology, stage="prod")

    assert prod_exports["POLARIS_WAREHOUSE"] == "lakehouse_prod"


def test_v2_contract_lifts_to_legacy_dev_without_changing_environment_output() -> None:
    contract = _fixture("local-provider-contracts.json")
    parsed = parse_provider_contracts(contract)
    exports, _ = build_contract_env({}, contract, repo_root=REPO_ROOT)

    assert parsed.compatibility_v2 is True
    assert parsed.for_stage().name.value == "dev"
    assert exports["OPENLAKEFORGE_CATALOG_NAME"] == "lakehouse_dev"
