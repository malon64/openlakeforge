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

    with pytest.raises(ProviderContractError, match="reporting.service_ref must be 'stage/dev/reporting'"):
        parse_provider_contracts(contract, topology)


def test_dev_orchestration_endpoint_cannot_reference_prod() -> None:
    contract = _fixture("aws-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["orchestration"]["endpoint_ref"] = "stage/prod/endpoints/orchestration"
    contract["stages"]["dev"]["endpoints"]["orchestration"] = "stage/prod/endpoints/orchestration"

    with pytest.raises(
        ProviderContractError, match="orchestration.endpoint_ref must be 'stage/dev/endpoints/orchestration'"
    ):
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


def test_polaris_warehouse_must_match_its_own_physical_identity() -> None:
    """A Polaris catalog's warehouse is exported to the runtime by
    as_v2_environment_contract(); a stage could keep a distinct physical_id
    while pointing warehouse at another stage's catalog and reach the wrong
    Polaris warehouse despite passing the duplicate-catalog-identity check."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["prod"]["catalog"]["warehouse"] = "lakehouse_dev"

    with pytest.raises(ProviderContractError, match="warehouse must match its own physical identity"):
        parse_provider_contracts(contract, topology)


@pytest.mark.parametrize("capability_key", ["reporting", "governance"])
def test_endpoint_leak_is_rejected_when_the_capability_is_disabled(capability_key: str) -> None:
    """endpoints.<capability> must not survive when the capability binding
    itself is absent - otherwise a slim stage can still publish (and have a
    consumer resolve) a stray or cross-stage endpoint reference."""
    contract = _fixture("aws-provider-contracts-v3.json")
    contract["stages"]["prod"].pop(capability_key, None)
    contract["stages"]["prod"]["endpoints"][capability_key] = f"stage/prod/endpoints/orphan-{capability_key}"
    topology = _topology(contract)

    with pytest.raises(ProviderContractError, match=f"endpoints.{capability_key} must be absent"):
        parse_provider_contracts(contract, topology)


def test_query_endpoint_must_be_a_non_empty_string() -> None:
    """A schema-invalid non-string endpoint must fail closed with
    ProviderContractError at the parser boundary, not crash later in
    build_contract_env's endpoint.startswith("http://") with AttributeError."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["query"]["endpoint"] = 1

    with pytest.raises(ProviderContractError, match="query.endpoint must be a non-empty string"):
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


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("bucket_name", 12345, "bucket_name must be a non-empty string"),
        ("bucket_name", ["not-hashable"], "bucket_name must be a non-empty string"),
        ("uri", ["not-a-string"], "uri must be a string"),
    ],
)
def test_storage_layer_fields_must_be_the_declared_type(field: str, value, match: str) -> None:
    """A malformed physical_id was already caught by _string(); bucket_name and
    uri were not, so a non-string value silently reached as_v2_environment_contract()
    (an empty/wrong-typed bucket) or crashed the cross-stage dedupe set with
    TypeError on an unhashable value."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["storage"]["bronze"][field] = value

    with pytest.raises(ProviderContractError, match=match):
        parse_provider_contracts(contract, topology)


def test_query_service_ref_must_be_the_shared_query_service_specifically() -> None:
    """query.service_ref resolving to *any* shared/* binding (e.g. shared/ops_storage)
    would let a stage's query service masquerade as something else entirely; the
    documented shared-query exception is specifically shared.query."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["query"]["service_ref"] = "shared/ops_storage"
    contract["stages"]["dev"]["endpoints"]["query"] = "shared/ops_storage"

    with pytest.raises(ProviderContractError, match="must reference the shared query service"):
        parse_provider_contracts(contract, topology)


def test_catalog_service_ref_must_be_the_shared_catalog_service_specifically() -> None:
    """Same failure mode as query.service_ref: a Polaris stage could point its
    catalog service_ref at any resolvable shared/* binding and the StageContract
    would advertise that unrelated service as its catalog."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["catalog"]["service_ref"] = "shared/ops_storage"
    contract["stages"]["dev"]["endpoints"]["catalog"] = "shared/ops_storage"

    with pytest.raises(ProviderContractError, match="must reference the shared catalog service"):
        parse_provider_contracts(contract, topology)


def test_query_endpoint_must_agree_across_stages() -> None:
    """shared.query is one Trino service used by every stage, isolated by
    catalog rather than by endpoint; a stage that keeps service_ref: shared/query
    but declares a different endpoint would silently be routed to a different
    Trino host despite passing the shared-service-reference check."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["prod"]["query"]["endpoint"] = "http://other-trino:8080"

    with pytest.raises(ProviderContractError, match="query.endpoint must match the shared query service"):
        parse_provider_contracts(contract, topology)


def test_query_endpoint_must_match_the_shared_binding_even_if_every_stage_agrees() -> None:
    """The prior cross-stage-equality check only compared stages to each
    other; every stage agreeing on a value that still diverges from
    shared.query's own declared endpoint must still fail closed."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    for stage in contract["stages"].values():
        stage["query"]["endpoint"] = "http://other-trino:8080"

    with pytest.raises(ProviderContractError, match="query.endpoint must match the shared query service"):
        parse_provider_contracts(contract, topology)


def test_catalog_rest_uri_must_match_the_shared_catalog_services_endpoint() -> None:
    """as_v2_environment_contract() exports catalog.rest_uri to runtime
    consumers; a stage could keep a valid service_ref while pointing rest_uri
    at a different Polaris deployment entirely."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["catalog"]["rest_uri"] = "http://other-polaris:8181/api/catalog"

    with pytest.raises(ProviderContractError, match="rest_uri must match the shared catalog service's endpoint"):
        parse_provider_contracts(contract, topology)


def test_catalog_token_uri_must_be_derived_from_its_own_rest_uri() -> None:
    """floe.py renders catalog.token_uri as oauth2_server_uri alongside the
    client-credential Secret references; a stage could keep the correct,
    anchored rest_uri while pointing token_uri at an unrelated service and
    post credentials there instead."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["catalog"]["token_uri"] = "http://other-idp:9000/oauth/tokens"

    with pytest.raises(ProviderContractError, match="token_uri must be derived from its own rest_uri"):
        parse_provider_contracts(contract, topology)


@pytest.mark.parametrize(
    "port",
    ["invalid", 99999, 0, True, 8333.5],
)
def test_s3_service_port_must_be_a_valid_tcp_port(port) -> None:
    """artifact_store.artifact_storage_client() and the port-forward path in
    commands/artifacts.py both call int() on the exported value without
    their own validation; a malformed port must fail closed here instead of
    with an uncaught ValueError downstream."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["storage"]["s3_service_port"] = port

    with pytest.raises(ProviderContractError, match="s3_service_port must be a valid TCP port"):
        parse_provider_contracts(contract, topology)


@pytest.mark.parametrize(
    ("endpoint", "match"),
    [
        ("https://trino.example:8443", "must be an http:// URI"),
        ("http://trino", "must be http://<host>:<port>"),
        ("http://trino:notaport", "must be http://<host>:<port>"),
    ],
)
def test_query_endpoint_must_be_a_shape_the_runtime_adapter_can_parse(endpoint: str, match: str) -> None:
    """olf.contracts._apply_provider_contracts only extracts host:port from an
    http:// endpoint; any other scheme or a missing/non-numeric port leaves
    OPENLAKEFORGE_QUERY_TRINO_HOST/PORT at their local defaults instead of
    routing to the declared service, silently."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    for stage in contract["stages"].values():
        stage["query"]["endpoint"] = endpoint

    with pytest.raises(ProviderContractError, match=match):
        parse_provider_contracts(contract, topology)


def test_shared_binding_ref_must_be_its_own_canonical_name() -> None:
    """_parse_shared only checked that each binding's ref started with
    'shared/', not that it named the binding itself; a mistakenly wrong
    shared.ops_storage.ref would not resolve through the hardcoded
    activation.ops_storage_ref == 'shared/ops_storage' check, but
    environment adaptation indexes the ops_storage key directly and would
    mask the mismatch instead of failing closed."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["shared"]["ops_storage"]["ref"] = "shared/foundation"

    with pytest.raises(ProviderContractError, match="shared.ops_storage.ref must be 'shared/ops_storage'"):
        parse_provider_contracts(contract, topology)


def test_orchestration_service_ref_cannot_be_another_same_stage_binding() -> None:
    """The prior stage/<name>/* prefix check accepted ANY same-stage binding,
    so orchestration.service_ref could point at the stage's own catalog and
    the StageContract would advertise that catalog as its orchestration
    service - ADR 0011 classifies orchestration as a per-stage service with
    one canonical name, not a free-form same-stage path."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["orchestration"]["service_ref"] = "stage/dev/catalog"

    with pytest.raises(ProviderContractError, match="orchestration.service_ref must be 'stage/dev/orchestration'"):
        parse_provider_contracts(contract, topology)


@pytest.mark.parametrize(
    "service_ref",
    ["shared/ops_storage", "stage/dev/orchestration"],
)
def test_reporting_service_ref_cannot_be_shared_or_another_same_stage_binding(service_ref: str) -> None:
    """reporting.service_ref previously accepted any resolvable shared/*
    binding via _stage_or_shared_reference, contradicting ADR 0011's
    classification of reporting as per-stage only (never shared) - and, like
    orchestration, also accepted another same-stage binding entirely."""
    contract = _fixture("aws-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["reporting"]["service_ref"] = service_ref

    with pytest.raises(ProviderContractError, match="reporting.service_ref must be 'stage/dev/reporting'"):
        parse_provider_contracts(contract, topology)


@pytest.mark.parametrize(
    ("fixture_name", "catalog_type", "catalog_provider"),
    [
        ("local-provider-contracts-v3.json", "glue", "aws-glue"),
        ("aws-provider-contracts-v3.json", "rest", "polaris"),
    ],
)
def test_catalog_provider_must_match_the_deployment_topology_provider(
    fixture_name: str, catalog_type: str, catalog_provider: str
) -> None:
    """catalog_type/catalog_provider pairing alone doesn't stop a local/Azure
    topology from accepting a well-formed Glue catalog, or an AWS topology
    from accepting Polaris if a catalog_service happens to be supplied - ADR
    0010 binds Polaris to local/Azure and Glue to AWS specifically."""
    contract = _fixture(fixture_name)
    topology = _topology(contract)
    contract["stages"]["dev"]["catalog"]["catalog_type"] = catalog_type
    contract["stages"]["dev"]["catalog"]["catalog_provider"] = catalog_provider
    if catalog_provider == "polaris":
        contract["stages"]["dev"]["catalog"]["service_ref"] = "shared/catalog_service"

    with pytest.raises(ProviderContractError, match="does not match DeploymentTopology.provider"):
        parse_provider_contracts(contract, topology)


@pytest.mark.parametrize(
    ("fixture_name", "implementation"),
    [
        ("aws-provider-contracts-v3.json", "storage.s3_compatible.seaweedfs"),
        ("local-provider-contracts-v3.json", "storage.aws_s3"),
    ],
)
def test_storage_implementation_must_match_the_deployment_topology_provider(
    fixture_name: str, implementation: str
) -> None:
    """storage.provider alone doesn't stop an AWS stage from retaining a
    SeaweedFS implementation (so build_contract_env's storage.aws_s3 branch
    at contracts.py never fires and local S3-compatible endpoint/credential
    defaults leak into an AWS deployment), or a local stage from claiming
    storage.aws_s3."""
    contract = _fixture(fixture_name)
    topology = _topology(contract)
    contract["stages"]["dev"]["storage"]["implementation"] = implementation

    with pytest.raises(ProviderContractError, match="storage.implementation must match DeploymentTopology.provider"):
        parse_provider_contracts(contract, topology)


def test_catalog_physical_id_must_be_a_non_empty_string() -> None:
    """catalog.physical_id feeds the cross-stage catalog_ids dedupe set in
    _parse_v3 without prior type validation, unlike storage physical_id, which
    already used _string(); an unhashable value there would crash with
    TypeError instead of failing closed."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["catalog"]["physical_id"] = ["not-hashable"]

    with pytest.raises(ProviderContractError, match="catalog.physical_id must be a non-empty string"):
        parse_provider_contracts(contract, topology)


def test_glue_rest_warehouse_must_match_its_own_glue_catalog_id() -> None:
    """as_v2_environment_contract() exports glue_rest_warehouse to the runtime;
    a stage could keep a valid, distinct glue_catalog_id/physical_id while
    pointing glue_rest_warehouse at another stage's Glue catalog."""
    contract = _fixture("aws-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["prod"]["catalog"]["glue_rest_warehouse"] = contract["stages"]["dev"]["catalog"][
        "glue_catalog_id"
    ]

    with pytest.raises(ProviderContractError, match="glue_rest_warehouse must match its own Glue catalog ID"):
        parse_provider_contracts(contract, topology)


def test_duplicate_namespace_across_stages_is_rejected() -> None:
    """Each stage is documented as owning its Kubernetes namespace; two stages
    sharing one namespace would let stage-specific orchestration and artifact
    operations target the same resource scope."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["prod"]["namespace"] = contract["stages"]["dev"]["namespace"]

    with pytest.raises(ProviderContractError, match="namespace .* is shared between stages"):
        parse_provider_contracts(contract, topology)


def test_governance_service_ref_must_be_the_shared_governance_service_specifically() -> None:
    """Same failure mode as query.service_ref and catalog.service_ref: a
    governance-enabled stage could point governance.service_ref at any
    resolvable shared/* binding instead of shared.governance_service."""
    contract = _fixture("aws-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["governance"]["service_ref"] = "shared/ops_storage"

    with pytest.raises(ProviderContractError, match="must reference the shared governance service"):
        parse_provider_contracts(contract, topology)


@pytest.mark.parametrize(
    ("principal", "match"),
    [
        (["not-hashable"], "principal must be a non-empty string"),
        ("", "principal must be a non-empty string"),
    ],
)
def test_runtime_identity_principal_must_be_a_non_empty_string(principal, match: str) -> None:
    """runtime_identity.principal feeds the cross-stage principals dedupe set
    in _parse_v3 without prior type validation; an unhashable value would
    crash with TypeError, and an empty string would pass a single-stage
    topology's dedupe check entirely."""
    contract = _fixture("local-provider-contracts-v3.json")
    topology = _topology(contract)
    contract["stages"]["dev"]["runtime_identity"]["principal"] = principal

    with pytest.raises(ProviderContractError, match=match):
        parse_provider_contracts(contract, topology)


def test_glue_stage_does_not_leak_local_polaris_token_and_scope_aliases() -> None:
    """The POLARIS_* compatibility aliases were re-derived before the is_glue
    normalization cleared OPENLAKEFORGE_CATALOG_TOKEN_URI/OAUTH_SCOPE, so an
    AWS/Glue stage exported the local Polaris dev defaults through
    POLARIS_TOKEN_URI/POLARIS_OAUTH_SCOPE while the canonical variables were
    correctly blank."""
    contract = _fixture("aws-provider-contracts-v3.json")
    topology = _topology(contract)

    exports, _ = build_contract_env({}, contract, repo_root=REPO_ROOT, topology=topology, stage="dev")

    assert exports["OPENLAKEFORGE_CATALOG_TOKEN_URI"] == ""
    assert exports["POLARIS_TOKEN_URI"] == ""
    assert exports["OPENLAKEFORGE_CATALOG_OAUTH_SCOPE"] == ""
    assert exports["POLARIS_OAUTH_SCOPE"] == ""


def test_v2_contract_lifts_to_legacy_dev_without_changing_environment_output() -> None:
    contract = _fixture("local-provider-contracts.json")
    parsed = parse_provider_contracts(contract)
    exports, _ = build_contract_env({}, contract, repo_root=REPO_ROOT)

    assert parsed.compatibility_v2 is True
    assert parsed.for_stage().name.value == "dev"
    assert exports["OPENLAKEFORGE_CATALOG_NAME"] == "lakehouse_dev"
