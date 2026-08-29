"""Stage-aware provider-contract parsing and compatibility adaptation.

Terraform remains the source of physical provider values. This module owns the
typed boundary that validates those values against a resolved deployment
topology before a runtime consumes them.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from olf.deployment.context import Provider
from olf.profile import DeploymentTopology, StageName

V2_SCHEMA_VERSION = "2.0.0"
V3_SCHEMA_VERSION = "3.0.0"
SUPPORTED_SCHEMA_VERSIONS = frozenset({V2_SCHEMA_VERSION, V3_SCHEMA_VERSION})


class ProviderContractError(ValueError):
    """Raised when a provider contract is malformed or cannot serve a stage."""


def aws_catalog_name(profile_name: str, stage: StageName | str) -> str:
    """Return the provider-derived, Glue-safe catalog name for one stage."""
    stage_value = StageName(stage).value
    candidate = f"olf_{profile_name.replace('-', '_')}_{stage_value}".lower()
    if len(candidate) <= 64:
        return candidate
    digest = hashlib.sha256(candidate.encode()).hexdigest()[:8]
    suffix = f"_{stage_value}_{digest}"
    return f"{candidate[: 64 - len(suffix)]}{suffix}"


def _mapping(value: object, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProviderContractError(f"{where} must be an object")
    return value


def _string(value: object, *, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProviderContractError(f"{where} must be a non-empty string")
    return value


def _fields(
    value: object,
    *,
    where: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> Mapping[str, Any]:
    document = _mapping(value, where=where)
    missing = required - set(document)
    unexpected = set(document) - required - optional
    if missing:
        raise ProviderContractError(f"{where} is missing required fields {sorted(missing)!r}")
    if unexpected:
        raise ProviderContractError(f"{where} contains unsupported fields {sorted(unexpected)!r}")
    return document


def _frozen(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_frozen(item) for item in value)
    return value


def _stage_name(value: StageName | str) -> StageName:
    try:
        return StageName(value)
    except ValueError as exc:
        raise ProviderContractError(f"unknown stage {value!r}") from exc


def _reference(value: object, *, where: str, allowed: tuple[str, ...]) -> str:
    reference = _string(value, where=where)
    if not reference.startswith(allowed):
        allowed_text = ", ".join(allowed)
        raise ProviderContractError(f"{where} must reference one of {allowed_text}")
    return reference


_CATALOG_TYPES = frozenset({"rest", "glue"})
_CATALOG_PROVIDERS = frozenset({"polaris", "aws-glue"})
_CATALOG_TYPE_BY_PROVIDER = {"polaris": "rest", "aws-glue": "glue"}
_CATALOG_PROVIDER_BY_TOPOLOGY_PROVIDER = {
    Provider.LOCAL: "polaris",
    Provider.AZURE: "polaris",
    Provider.AWS: "aws-glue",
}


def _stage_or_shared_reference(
    value: object, *, where: str, stage: StageName, shared_refs: set[str]
) -> str:
    """A binding that may point at the owning stage or a resolvable shared service.

    Used for the handful of fields (e.g. reporting.service_ref) that may be
    satisfied by either a stage-local implementation or a platform-wide one,
    unlike endpoint_ref fields, which are always stage-scoped.
    """
    reference = _string(value, where=where)
    if reference.startswith(f"stage/{stage.value}/"):
        return reference
    if reference in shared_refs:
        return reference
    raise ProviderContractError(f"{where} must reference stage/{stage.value}/* or a resolvable shared/* binding")


@dataclass(frozen=True)
class SharedPlatformContract:
    """Provider-owned services that must not be repeated for every stage."""

    values: Mapping[str, Mapping[str, Any]]


@dataclass(frozen=True)
class StageContract:
    """One selected stage's provider bindings and runtime environment shape."""

    name: StageName
    namespace: str
    storage: Mapping[str, Any]
    catalog: Mapping[str, Any]
    query: Mapping[str, Any]
    orchestration: Mapping[str, Any]
    activation: Mapping[str, Any]
    endpoints: Mapping[str, Any]
    runtime_identity: Mapping[str, Any]
    reporting: Mapping[str, Any] | None
    governance: Mapping[str, Any] | None
    shared: SharedPlatformContract

    def as_v2_environment_contract(self) -> dict[str, Any]:
        """Adapt a selected v3 stage to the existing runtime environment API."""
        layers = {
            layer: _mapping(self.storage[layer], where=f"storage.{layer}") for layer in ("bronze", "silver", "gold")
        }
        storage = {
            key: value for key, value in self.storage.items() if key not in {"bronze", "silver", "gold", "identity_ref"}
        }
        storage.update(
            {
                "bronze_bucket_name": layers["bronze"]["bucket_name"],
                "silver_bucket_name": layers["silver"]["bucket_name"],
                "gold_bucket_name": layers["gold"]["bucket_name"],
                "bucket_name": layers["bronze"]["bucket_name"],
            }
        )
        catalog = {key: value for key, value in self.catalog.items() if key not in {"physical_id", "service_ref"}}
        ops_storage = self.shared.values["ops_storage"]
        base_uri = _string(ops_storage["artifact_base_uri"], where="shared.ops_storage.artifact_base_uri").rstrip("/")
        prefix = _string(self.activation["prefix"], where=f"stages.{self.name}.activation.prefix").strip("/")
        stage_uri = f"{base_uri}/{prefix}"
        return {
            "schema_version": V2_SCHEMA_VERSION,
            "storage": storage,
            "catalog": catalog,
            "artifact_bucket": {
                "bucket_name": ops_storage["bucket_name"],
                "artifact_base_uri": stage_uri,
                "access_mode": ops_storage.get("access_mode", "remote"),
                "base_uri": f"{stage_uri}/floe/manifests",
                "floe_report_base_uri": f"{stage_uri}/floe/reports",
                "log_base_uri": f"{stage_uri}/logs",
                "run_artifact_base_uri": f"{stage_uri}/run-artifacts",
                "local_upload_access_mode": ops_storage.get("local_upload_access_mode", "direct"),
            },
            "kubernetes_platform": {"namespace": self.namespace},
            "query": {"catalog_name": self.query["catalog_name"], "endpoint": self.query["endpoint"]},
            "governance": {"enabled": self.governance is not None},
            "reporting": {"enabled": self.reporting is not None},
        }


@dataclass(frozen=True)
class ProviderContracts:
    """A provider contract that can expose only explicitly enabled stages."""

    schema_version: str
    deployment: Mapping[str, Any]
    shared: SharedPlatformContract
    stages: Mapping[StageName, StageContract]
    compatibility_v2: bool = False

    def for_stage(self, stage: StageName | str | None = None) -> StageContract:
        if stage is None:
            if self.compatibility_v2:
                return self.stages[StageName.DEV]
            raise ProviderContractError("native provider-contract v3 requires an explicit stage")
        stage_name = _stage_name(stage)
        try:
            return self.stages[stage_name]
        except KeyError as exc:
            raise ProviderContractError(f"provider contract has no enabled {stage_name.value!r} stage") from exc


def _parse_shared(value: object) -> SharedPlatformContract:
    required = {
        "foundation",
        "kubernetes_platform",
        "metadata_database",
        "query",
        "artifact_registry",
        "ops_storage",
        "secrets",
        "identity",
        "access",
        "observability",
    }
    optional = {"catalog_service", "governance_service"}
    document = _fields(value, where="shared", required=required, optional=optional)
    parsed: dict[str, Mapping[str, Any]] = {}
    for name, binding in document.items():
        parsed[name] = _fields(
            binding,
            where=f"shared.{name}",
            required={"ref", "implementation"},
            optional={
                "endpoint",
                "bucket_name",
                "artifact_base_uri",
                "access_mode",
                "local_upload_access_mode",
            },
        )
        _reference(parsed[name]["ref"], where=f"shared.{name}.ref", allowed=("shared/",))
    ops_storage = parsed["ops_storage"]
    for field in ("bucket_name", "artifact_base_uri"):
        _string(ops_storage.get(field), where=f"shared.ops_storage.{field}")
    return SharedPlatformContract(values=_frozen(parsed))


def _parse_stage(
    name: StageName,
    value: object,
    *,
    shared: SharedPlatformContract,
    topology: DeploymentTopology,
) -> StageContract:
    shared_refs = {binding["ref"] for binding in shared.values.values()}
    document = _fields(
        value,
        where=f"stages.{name.value}",
        required={
            "namespace",
            "storage",
            "catalog",
            "query",
            "orchestration",
            "activation",
            "endpoints",
            "runtime_identity",
        },
        optional={"reporting", "governance"},
    )
    namespace = _string(document["namespace"], where=f"stages.{name.value}.namespace")
    storage = _fields(
        document["storage"],
        where=f"stages.{name.value}.storage",
        required={"provider", "implementation", "protocol", "region", "identity_ref", "bronze", "silver", "gold"},
        optional={
            "endpoint",
            "virtual_host_endpoint",
            "path_style_access",
            "ssl_mode",
            "credentials_secret_name",
            "access_key_id_key",
            "secret_access_key_key",
            "s3_service_name",
            "s3_service_port",
        },
    )
    if storage["provider"] != topology.provider.value:
        raise ProviderContractError(f"stages.{name.value}.storage.provider must match DeploymentTopology.provider")
    if topology.region is not None and storage["region"] != topology.region:
        raise ProviderContractError(f"stages.{name.value}.storage.region must match DeploymentTopology.region")
    _reference(storage["identity_ref"], where=f"stages.{name.value}.storage.identity_ref", allowed=("stage/",))
    physical_storage: set[str] = set()
    for layer in ("bronze", "silver", "gold"):
        binding = _fields(
            storage[layer],
            where=f"stages.{name.value}.storage.{layer}",
            required={"physical_id", "bucket_name", "uri"},
        )
        physical_id = _string(binding["physical_id"], where=f"stages.{name.value}.storage.{layer}.physical_id")
        if physical_id in physical_storage:
            raise ProviderContractError(f"stages.{name.value}.storage reuses physical identity {physical_id!r}")
        physical_storage.add(physical_id)
        _string(binding["bucket_name"], where=f"stages.{name.value}.storage.{layer}.bucket_name")
        if not isinstance(binding["uri"], str):
            raise ProviderContractError(f"stages.{name.value}.storage.{layer}.uri must be a string")
    catalog = _fields(
        document["catalog"],
        where=f"stages.{name.value}.catalog",
        required={
            "logical_name",
            "implementation",
            "catalog_type",
            "catalog_provider",
            "catalog_name",
            "runtime_profile",
            "physical_id",
        },
        optional={
            "service_ref",
            "warehouse",
            "rest_uri",
            "token_uri",
            "oauth_scope",
            "glue_region",
            "glue_catalog_id",
            "glue_rest_uri",
            "glue_rest_warehouse",
            "glue_warehouse_prefix",
            "catalog_namespace_model",
            "floe_credentials_secret_name",
            "floe_client_id_key",
            "floe_client_secret_key",
            "deployer_credentials_secret_name",
            "deployer_client_id_key",
            "deployer_client_secret_key",
        },
    )
    _string(catalog["physical_id"], where=f"stages.{name.value}.catalog.physical_id")
    catalog_type = catalog["catalog_type"]
    catalog_provider = catalog["catalog_provider"]
    if catalog_type not in _CATALOG_TYPES:
        raise ProviderContractError(
            f"stages.{name.value}.catalog.catalog_type must be one of {sorted(_CATALOG_TYPES)!r}"
        )
    if catalog_provider not in _CATALOG_PROVIDERS:
        raise ProviderContractError(
            f"stages.{name.value}.catalog.catalog_provider must be one of {sorted(_CATALOG_PROVIDERS)!r}"
        )
    if _CATALOG_TYPE_BY_PROVIDER[catalog_provider] != catalog_type:
        raise ProviderContractError(
            f"stages.{name.value}.catalog.catalog_type {catalog_type!r} does not match "
            f"catalog_provider {catalog_provider!r}"
        )
    if catalog_provider != _CATALOG_PROVIDER_BY_TOPOLOGY_PROVIDER[topology.provider]:
        raise ProviderContractError(
            f"stages.{name.value}.catalog.catalog_provider {catalog_provider!r} does not match "
            f"DeploymentTopology.provider {topology.provider.value!r}"
        )
    expected_catalog = f"lakehouse_{name.value}"
    if catalog["catalog_name"] != expected_catalog:
        raise ProviderContractError(f"stages.{name.value}.catalog.catalog_name must be canonical {expected_catalog!r}")
    if catalog["catalog_provider"] == "aws-glue":
        for field in ("glue_region", "glue_catalog_id"):
            _string(catalog.get(field), where=f"stages.{name.value}.catalog.{field}")
        if topology.region is not None and catalog["glue_region"] != topology.region:
            raise ProviderContractError(f"stages.{name.value}.catalog.glue_region must match DeploymentTopology.region")
        if catalog["glue_catalog_id"] != catalog["physical_id"]:
            raise ProviderContractError(f"stages.{name.value}.catalog Glue catalog ID must be its physical identity")
        if "glue_rest_warehouse" in catalog and catalog["glue_rest_warehouse"] != catalog["glue_catalog_id"]:
            raise ProviderContractError(
                f"stages.{name.value}.catalog.glue_rest_warehouse must match its own Glue catalog ID"
            )
        catalog_name = catalog["glue_catalog_id"].rsplit(":", 1)[-1]
        if catalog_name != aws_catalog_name(topology.profile_name, name):
            raise ProviderContractError(
                f"stages.{name.value}.catalog Glue catalog ID must end with "
                f"{aws_catalog_name(topology.profile_name, name)!r}"
            )
    elif "service_ref" not in catalog:
        raise ProviderContractError(f"stages.{name.value}.catalog.service_ref is required for a Polaris catalog")
    if "service_ref" in catalog:
        _reference(catalog["service_ref"], where=f"stages.{name.value}.catalog.service_ref", allowed=("shared/",))
        if catalog["service_ref"] not in shared_refs:
            raise ProviderContractError(f"stages.{name.value}.catalog.service_ref does not resolve")
        catalog_service_ref = shared.values.get("catalog_service", {}).get("ref")
        if catalog["service_ref"] != catalog_service_ref:
            raise ProviderContractError(
                f"stages.{name.value}.catalog.service_ref must reference the shared catalog service"
            )
    if "warehouse" in catalog and catalog["warehouse"] != catalog["physical_id"]:
        raise ProviderContractError(f"stages.{name.value}.catalog.warehouse must match its own physical identity")
    query = _fields(
        document["query"],
        where=f"stages.{name.value}.query",
        required={"service_ref", "catalog_ref", "catalog_name", "endpoint", "runtime_identity_ref"},
    )
    _string(query["endpoint"], where=f"stages.{name.value}.query.endpoint")
    _reference(query["service_ref"], where=f"stages.{name.value}.query.service_ref", allowed=("shared/",))
    if query["service_ref"] not in shared_refs:
        raise ProviderContractError(f"stages.{name.value}.query.service_ref does not resolve")
    if query["service_ref"] != shared.values["query"]["ref"]:
        raise ProviderContractError(f"stages.{name.value}.query.service_ref must reference the shared query service")
    if query["catalog_ref"] != f"stage/{name.value}/catalog":
        raise ProviderContractError(f"stages.{name.value}.query.catalog_ref cannot reference another stage")
    if query["catalog_name"] != expected_catalog:
        raise ProviderContractError(f"stages.{name.value}.query.catalog_name must be {expected_catalog!r}")
    orchestration = _fields(
        document["orchestration"],
        where=f"stages.{name.value}.orchestration",
        required={"service_ref", "endpoint_ref"},
    )
    _reference(
        orchestration["service_ref"],
        where=f"stages.{name.value}.orchestration.service_ref",
        allowed=(f"stage/{name.value}/",),
    )
    _reference(
        orchestration["endpoint_ref"],
        where=f"stages.{name.value}.orchestration.endpoint_ref",
        allowed=(f"stage/{name.value}/",),
    )
    activation = _fields(
        document["activation"],
        where=f"stages.{name.value}.activation",
        required={"ops_storage_ref", "prefix"},
    )
    if activation["ops_storage_ref"] != "shared/ops_storage":
        raise ProviderContractError(f"stages.{name.value}.activation must use shared ops storage")
    if activation["prefix"] != f"activations/{name.value}":
        raise ProviderContractError(f"stages.{name.value}.activation.prefix must be activations/{name.value!s}")
    endpoints = _fields(
        document["endpoints"],
        where=f"stages.{name.value}.endpoints",
        required={"catalog", "query", "orchestration"},
        optional={"reporting", "governance"},
    )
    if endpoints["query"] != query["service_ref"]:
        raise ProviderContractError(f"stages.{name.value}.endpoints.query must resolve the query service")
    if endpoints["orchestration"] != orchestration["endpoint_ref"]:
        raise ProviderContractError(f"stages.{name.value}.endpoints.orchestration must resolve orchestration")
    expected_catalog_endpoint = catalog["service_ref"] if "service_ref" in catalog else f"stage/{name.value}/catalog"
    if endpoints["catalog"] != expected_catalog_endpoint:
        raise ProviderContractError(f"stages.{name.value}.endpoints.catalog must resolve the stage catalog")
    runtime_identity = _fields(
        document["runtime_identity"],
        where=f"stages.{name.value}.runtime_identity",
        required={"ref", "principal"},
    )
    identity_ref = f"stage/{name.value}/runtime_identity"
    if runtime_identity["ref"] != identity_ref:
        raise ProviderContractError(f"stages.{name.value}.runtime_identity.ref must be {identity_ref!r}")
    _string(runtime_identity["principal"], where=f"stages.{name.value}.runtime_identity.principal")
    if storage["identity_ref"] != identity_ref or query["runtime_identity_ref"] != identity_ref:
        raise ProviderContractError(f"stages.{name.value} runtime bindings must use their own runtime identity")

    resolved_stage = topology.stage(name)
    if resolved_stage is None:
        raise ProviderContractError(f"DeploymentTopology has no {name.value!r} stage")
    reporting = document.get("reporting")
    governance = document.get("governance")
    if resolved_stage.capabilities.analytics != (reporting is not None):
        raise ProviderContractError(f"stages.{name.value}.reporting must match the analytics capability")
    if resolved_stage.capabilities.governance != (governance is not None):
        raise ProviderContractError(f"stages.{name.value}.governance must match the governance capability")
    if reporting is not None:
        reporting = _fields(
            reporting,
            where=f"stages.{name.value}.reporting",
            required={"service_ref", "endpoint_ref"},
        )
        _stage_or_shared_reference(
            reporting["service_ref"],
            where=f"stages.{name.value}.reporting.service_ref",
            stage=name,
            shared_refs=shared_refs,
        )
        _reference(
            reporting["endpoint_ref"],
            where=f"stages.{name.value}.reporting.endpoint_ref",
            allowed=(f"stage/{name.value}/",),
        )
        if endpoints.get("reporting") != reporting["endpoint_ref"]:
            raise ProviderContractError(f"stages.{name.value}.endpoints.reporting must resolve reporting")
    elif "reporting" in endpoints:
        raise ProviderContractError(f"stages.{name.value}.endpoints.reporting must be absent without reporting")
    if governance is not None:
        governance = _fields(
            governance,
            where=f"stages.{name.value}.governance",
            required={"service_ref", "endpoint_ref"},
        )
        if governance["service_ref"] not in shared_refs:
            raise ProviderContractError(f"stages.{name.value}.governance.service_ref does not resolve")
        governance_service_ref = shared.values.get("governance_service", {}).get("ref")
        if governance["service_ref"] != governance_service_ref:
            raise ProviderContractError(
                f"stages.{name.value}.governance.service_ref must reference the shared governance service"
            )
        _reference(
            governance["endpoint_ref"],
            where=f"stages.{name.value}.governance.endpoint_ref",
            allowed=(f"stage/{name.value}/",),
        )
        if endpoints.get("governance") != governance["endpoint_ref"]:
            raise ProviderContractError(f"stages.{name.value}.endpoints.governance must resolve governance")
    elif "governance" in endpoints:
        raise ProviderContractError(f"stages.{name.value}.endpoints.governance must be absent without governance")
    return StageContract(
        name=name,
        namespace=namespace,
        storage=_frozen(storage),
        catalog=_frozen(catalog),
        query=_frozen(query),
        orchestration=_frozen(orchestration),
        activation=_frozen(activation),
        endpoints=_frozen(endpoints),
        runtime_identity=_frozen(runtime_identity),
        reporting=_frozen(reporting) if reporting is not None else None,
        governance=_frozen(governance) if governance is not None else None,
        shared=shared,
    )


def _parse_v3(payload: Mapping[str, Any], topology: DeploymentTopology | None) -> ProviderContracts:
    if topology is None:
        raise ProviderContractError("native provider-contract v3 requires a resolved DeploymentTopology")
    document = _fields(
        payload, where="provider_contracts", required={"schema_version", "deployment", "shared", "stages"}
    )
    deployment = _fields(
        document["deployment"],
        where="deployment",
        required={"profile_name", "provider", "region"},
    )
    try:
        provider = Provider(_string(deployment["provider"], where="deployment.provider"))
    except ValueError as exc:
        raise ProviderContractError("deployment.provider is unsupported") from exc
    if provider != topology.provider:
        raise ProviderContractError("deployment.provider must match DeploymentTopology.provider")
    if deployment["profile_name"] != topology.profile_name:
        raise ProviderContractError("deployment.profile_name must match DeploymentTopology.profile_name")
    if deployment["region"] != topology.region:
        raise ProviderContractError("deployment.region must match DeploymentTopology.region")
    shared = _parse_shared(document["shared"])
    stages_document = _mapping(document["stages"], where="stages")
    expected_names = {stage.name.value for stage in topology.stages if stage.enabled}
    actual_names = set(stages_document)
    if actual_names != expected_names:
        raise ProviderContractError(
            f"contract stages {sorted(actual_names)!r} must equal enabled topology stages {sorted(expected_names)!r}"
        )
    stages: dict[StageName, StageContract] = {}
    physical_storage: set[str] = set()
    storage_bucket_names: set[str] = set()
    storage_uris: set[str] = set()
    catalog_ids: set[str] = set()
    principals: set[str] = set()
    stage_endpoint_values: set[str] = set()
    namespaces: set[str] = set()
    # Anchor to shared.query's own endpoint when the binding declares one (AWS's
    # fixture does not, so the first stage's endpoint is the anchor there) -
    # otherwise every stage could agree on a value that still diverges from the
    # one shared Trino service the binding actually names.
    shared_query_endpoint: str | None = shared.values["query"].get("endpoint")
    for raw_name, stage_value in stages_document.items():
        name = _stage_name(raw_name)
        stage = _parse_stage(name, stage_value, shared=shared, topology=topology)
        stages[name] = stage
        if stage.namespace in namespaces:
            raise ProviderContractError(f"namespace {stage.namespace!r} is shared between stages")
        namespaces.add(stage.namespace)
        query_endpoint = stage.query["endpoint"]
        if shared_query_endpoint is None:
            shared_query_endpoint = query_endpoint
        elif query_endpoint != shared_query_endpoint:
            raise ProviderContractError(f"stages.{name.value}.query.endpoint must match the shared query service")
        for layer in ("bronze", "silver", "gold"):
            layer_binding = stage.storage[layer]
            physical_id = layer_binding["physical_id"]
            if physical_id in physical_storage:
                raise ProviderContractError(f"storage physical identity {physical_id!r} is shared between stages")
            physical_storage.add(physical_id)
            bucket_name = layer_binding["bucket_name"]
            if bucket_name in storage_bucket_names:
                raise ProviderContractError(f"storage bucket name {bucket_name!r} is shared between stages")
            storage_bucket_names.add(bucket_name)
            uri = layer_binding["uri"]
            if uri and uri in storage_uris:
                raise ProviderContractError(f"storage location {uri!r} is shared between stages")
            if uri:
                storage_uris.add(uri)
        physical_id = stage.catalog["physical_id"]
        if physical_id in catalog_ids:
            raise ProviderContractError(f"catalog physical identity {physical_id!r} is shared between stages")
        catalog_ids.add(physical_id)
        principal = stage.runtime_identity["principal"]
        if principal in principals:
            raise ProviderContractError(f"runtime principal {principal!r} is shared between stages")
        principals.add(principal)
        for endpoint_name in ("orchestration", "reporting", "governance"):
            endpoint = stage.endpoints.get(endpoint_name)
            if endpoint is None:
                continue
            if endpoint in stage_endpoint_values:
                raise ProviderContractError(f"stage endpoint {endpoint!r} is shared between stages")
            stage_endpoint_values.add(endpoint)
    return ProviderContracts(
        schema_version=V3_SCHEMA_VERSION,
        deployment=_frozen(deployment),
        shared=shared,
        stages=MappingProxyType(stages),
    )


def _adapt_v2(payload: Mapping[str, Any]) -> ProviderContracts:
    """Lift the legacy flat contract to its one enabled DEV-stage equivalent."""
    storage = _mapping(payload.get("storage", {}), where="provider_contracts.storage")
    catalog = _mapping(payload.get("catalog", {}), where="provider_contracts.catalog")
    query = _mapping(payload.get("query", {}), where="provider_contracts.query")
    artifacts = _mapping(
        payload.get("artifact_bucket", payload.get("artifacts", {})), where="provider_contracts.artifact_bucket"
    )
    platform = _mapping(
        payload.get("kubernetes_platform", payload.get("cluster", {})), where="provider_contracts.kubernetes_platform"
    )
    provider = storage.get("provider", "local")
    shared_values = {
        "foundation": {"ref": "shared/foundation", "implementation": "legacy"},
        "kubernetes_platform": {"ref": "shared/kubernetes_platform", "implementation": "legacy"},
        "metadata_database": {"ref": "shared/metadata_database", "implementation": "legacy"},
        "query": {"ref": "shared/query", "implementation": "legacy"},
        "artifact_registry": {"ref": "shared/artifact_registry", "implementation": "legacy"},
        "ops_storage": {
            "ref": "shared/ops_storage",
            "implementation": "legacy",
            "bucket_name": artifacts.get("bucket_name", artifacts.get("ops_bucket_name", "openlakeforge-ops")),
            "artifact_base_uri": artifacts.get("artifact_base_uri", "s3://openlakeforge-ops"),
            "access_mode": artifacts.get("access_mode", "remote"),
            "local_upload_access_mode": artifacts.get("local_upload_access_mode", "direct"),
        },
        "secrets": {"ref": "shared/secrets", "implementation": "legacy"},
        "identity": {"ref": "shared/identity", "implementation": "legacy"},
        "access": {"ref": "shared/access", "implementation": "legacy"},
        "observability": {"ref": "shared/observability", "implementation": "legacy"},
    }
    shared = SharedPlatformContract(values=_frozen(shared_values))
    stage = StageContract(
        name=StageName.DEV,
        namespace=str(platform.get("namespace", "lakehouse")),
        storage=_frozen(
            {
                **storage,
                "identity_ref": "stage/dev/runtime_identity",
                "bronze": {
                    "physical_id": storage.get("bronze_bucket_name", "lakehouse-bronze"),
                    "bucket_name": storage.get("bronze_bucket_name", "lakehouse-bronze"),
                    "uri": "",
                },
                "silver": {
                    "physical_id": storage.get("silver_bucket_name", "lakehouse-silver"),
                    "bucket_name": storage.get("silver_bucket_name", "lakehouse-silver"),
                    "uri": "",
                },
                "gold": {
                    "physical_id": storage.get("gold_bucket_name", "lakehouse-gold"),
                    "bucket_name": storage.get("gold_bucket_name", "lakehouse-gold"),
                    "uri": "",
                },
            }
        ),
        catalog=_frozen(
            {**catalog, "physical_id": catalog.get("glue_catalog_id", catalog.get("catalog_name", "lakehouse_dev"))}
        ),
        query=_frozen(
            {
                "service_ref": "shared/query",
                "catalog_ref": "stage/dev/catalog",
                "catalog_name": query.get("catalog_name", "iceberg"),
                "endpoint": query.get("endpoint", "http://trino:8080"),
                "runtime_identity_ref": "stage/dev/runtime_identity",
            }
        ),
        orchestration=_frozen({"service_ref": "stage/dev/orchestration", "endpoint_ref": "legacy"}),
        activation=_frozen({"ops_storage_ref": "shared/ops_storage", "prefix": "activations/dev"}),
        endpoints=_frozen(
            {"catalog": "legacy-catalog", "query": "legacy-query", "orchestration": "legacy-orchestration"}
        ),
        runtime_identity=_frozen({"ref": "stage/dev/runtime_identity", "principal": f"legacy-{provider}-dev"}),
        reporting=None,
        governance=None,
        shared=shared,
    )
    return ProviderContracts(
        schema_version=V3_SCHEMA_VERSION,
        deployment=MappingProxyType({"profile_name": "legacy", "provider": provider, "region": storage.get("region")}),
        shared=shared,
        stages=MappingProxyType({StageName.DEV: stage}),
        compatibility_v2=True,
    )


def parse_provider_contracts(
    payload: Mapping[str, Any], topology: DeploymentTopology | None = None
) -> ProviderContracts:
    """Parse v2/v3 provider output, rejecting unknown versions and shapes."""
    schema_version = payload.get("schema_version")
    if schema_version == V2_SCHEMA_VERSION:
        return _adapt_v2(payload)
    if schema_version == V3_SCHEMA_VERSION:
        return _parse_v3(payload, topology)
    raise ProviderContractError(
        f"provider_contracts.schema_version {schema_version!r} is unsupported; "
        f"expected one of {sorted(SUPPORTED_SCHEMA_VERSIONS)!r}"
    )
