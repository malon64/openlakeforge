"""Behavioral provider-contract validation for `olf contracts check`.

Replaces `scripts/test/check-contracts.sh`'s source-text grepping with
structured checks: parsed Terraform HCL, the canonical domain model plus
JSON Schema, and parsed rendered Floe/Helm output. There is no shell left to
cover — `olf check structure` rejects tracked `.sh` files (ADR 0008). Deploy
phase ordering is covered separately by
`tools/olf/tests/test_deployment_engine.py`, not by this module.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import hcl2
import jsonschema
import yaml
from openlakeforge_domain import (
    LakehouseDescriptorError,
    load_lakehouse_descriptor,
    load_source_descriptor,
)

from olf import contracts as contracts_module
from olf import floe as floe_module

_SCHEMA_BY_KIND = {
    "lakehouse": "docs/schema/lakehouse.schema.json",
    "source": "docs/schema/source.schema.json",
}

# Terraform environment roots that carry a `contracts.tf` provider-contract
# surface (ADR-defined; local/azure-poc/aws-poc are the only ones today).
_ENVIRONMENT_ROOTS = ("local", "azure-poc", "aws-poc")

# Every `provider_contracts` capability local that `contracts.tf` must
# declare in each environment (order matches `provider_contracts`' keys).
_REQUIRED_CONTRACT_LOCALS = (
    "foundation_contract",
    "kubernetes_platform_contract",
    "storage_contract",
    "metadata_database_contract",
    "catalog_contract",
    "query_contract",
    "orchestration_contract",
    "governance_contract",
    "reporting_contract",
    "artifact_registry_contract",
    "artifact_bucket_contract",
    "artifact_contract",
    "secrets_contract",
    "identity_contract",
    "access_contract",
    "observability_contract",
    "provider_contracts",
)

# Cross-field invariants each environment's `contracts.tf` must declare as a
# native Terraform `check` block (ADR-defined; these only evaluate under
# `terraform plan`/`apply`, so this tier only confirms they are declared).
# The "adapters are explicit" check is named per-provider; the OpenMetadata
# FQN check only applies where a Polaris-backed catalog names a database.
_REQUIRED_CONTRACT_CHECKS_BY_ENV = {
    "local": (
        "foundation_contract_matches_platform_context",
        "local_contract_adapters_are_explicit",
        "catalog_contract_consumer_support",
        "openmetadata_catalog_fqn_uses_lakehouse_database",
    ),
    "azure-poc": (
        "foundation_contract_matches_platform_context",
        "azure_contract_adapters_are_explicit",
        "catalog_contract_consumer_support",
        "openmetadata_catalog_fqn_uses_lakehouse_database",
    ),
    "aws-poc": (
        "foundation_contract_matches_platform_context",
        "aws_contract_adapters_are_explicit",
        "catalog_contract_consumer_support",
    ),
}

# ADR 0002: catalog namespace/database lifecycle belongs to Phase 2
# (`olf catalog sync-namespaces`), never to Terraform-owned locals or module
# arguments in `main.tf`/`contracts.tf`.
_FORBIDDEN_PHASE_TWO_FIELDS = (
    "catalog_namespaces",
    "silver_namespaces",
    "gold_namespaces",
    "silver_schema_fqns",
    "gold_schema_fqns",
)


def _parse_hcl(path: Path) -> dict[str, Any]:
    return hcl2.loads(path.read_text(encoding="utf-8"))


def _merged_locals(document: dict[str, Any]) -> dict[str, Any]:
    """Flatten every `locals { }` block's name -> value map. Values built
    from `merge(module.X.contract, {...})` collapse to an opaque
    '${merge(...)}' expression string -- hcl2 parses HCL syntax, not
    Terraform semantics, so it never resolves module references or function
    calls. Callers that need those scope their check to that string."""
    merged: dict[str, Any] = {}
    for block in document.get("locals", []):
        merged.update(block)
    return merged


@dataclass
class CheckResult:
    """Outcome of a single contract check."""

    name: str
    ok: bool
    detail: str = ""


@dataclass
class ContractsCheckReport:
    """Aggregate result of `olf contracts check`."""

    results: list[CheckResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results)

    def render(self) -> str:
        lines = []
        for result in self.results:
            status = "PASS" if result.ok else "FAIL"
            detail = f": {result.detail}" if result.detail else ""
            lines.append(f"[{status}] {result.name}{detail}")
        return "\n".join(lines)


# The exact cardinality rules an `olf init --empty` project has not satisfied
# yet. `allow_incomplete` waives these three and nothing else, so a
# transitional project still fails on every other descriptor, identity, and
# reference rule.
_TRANSITIONAL_SCHEMA_GAPS: frozenset[tuple[tuple[str, ...], str]] = frozenset(
    {
        (("sources",), "minItems"),
        (("domains",), "minItems"),
        (("domains",), "contains"),
    }
)


def _is_transitional_gap(error: jsonschema.ValidationError) -> bool:
    location = tuple(str(part) for part in error.absolute_path)
    return (location, error.validator) in _TRANSITIONAL_SCHEMA_GAPS


def descriptor_schema_errors(
    repo_root: Path, *, schema_root: Path | None = None, allow_incomplete: bool = False
) -> list[str]:
    """Validate `lakehouse_code/lakehouse.yaml` and every
    `lakehouse_code/bronze/*/source.yaml` against the canonical model and
    versioned JSON Schema. The two validators run independently; neither one
    substitutes for the other. Returns an empty list when everything is
    valid. Shared by `olf contracts check` and the `olf source|domain|product
    new` scaffold engine's pre-commit verification.

    `schema_root` points at the directory holding the versioned JSON Schemas
    when they live outside `repo_root` — an installed distribution keeps them
    in its immutable payload while the descriptors live in the user project.
    `allow_incomplete` waives only the transitional cardinality gaps listed in
    `_TRANSITIONAL_SCHEMA_GAPS`."""
    lakehouse_path = repo_root / "lakehouse_code" / "lakehouse.yaml"
    source_paths = sorted((repo_root / "lakehouse_code" / "bronze").glob("*/source.yaml"))
    descriptor_paths = [lakehouse_path, *source_paths]
    if not lakehouse_path.is_file():
        return [f"no lakehouse.yaml found under {repo_root / 'lakehouse_code'}"]

    errors: list[str] = []
    for descriptor_path in descriptor_paths:
        rel_path = descriptor_path.relative_to(repo_root)
        kind = "lakehouse" if descriptor_path == lakehouse_path else "source"
        try:
            if kind == "lakehouse":
                document = load_lakehouse_descriptor(descriptor_path, allow_incomplete=allow_incomplete)
            else:
                document = load_source_descriptor(descriptor_path)
        except LakehouseDescriptorError as exc:
            errors.append(f"{rel_path}: canonical model rejected descriptor: {exc}")
            continue

        schema_relpath = _SCHEMA_BY_KIND[kind]
        schema_path = schema_root / Path(schema_relpath).name if schema_root is not None else repo_root / schema_relpath
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        schema_errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
        if allow_incomplete:
            schema_errors = [error for error in schema_errors if not _is_transitional_gap(error)]
        for error in schema_errors:
            location = "/".join(str(part) for part in error.absolute_path) or "<root>"
            errors.append(f"{rel_path}: schema violation at {location}: {error.message}")

    return errors


def _check_descriptor_schema_conformance(repo_root: Path, *, schema_root: Path | None = None) -> CheckResult:
    """The `lakehouse_code/lakehouse.yaml` descriptor and every
    `lakehouse_code/bronze/*/source.yaml` must load via the canonical model
    and conform to its versioned JSON Schema. The two validators run
    independently; neither one substitutes for the other.

    `schema_root` points at the distribution payload's `docs/schema/` for an
    installed project, whose `lakehouse_code/` has no `docs/schema/` of its
    own (ADR 0009)."""
    name = "descriptor_schema_conformance"
    lakehouse_path = repo_root / "lakehouse_code" / "lakehouse.yaml"
    source_paths = sorted((repo_root / "lakehouse_code" / "bronze").glob("*/source.yaml"))
    descriptor_count = 1 + len(source_paths) if lakehouse_path.is_file() else 0

    errors = descriptor_schema_errors(repo_root, schema_root=schema_root)
    if errors:
        return CheckResult(name, ok=False, detail="; ".join(errors))
    return CheckResult(name, ok=True, detail=f"{descriptor_count} descriptor(s) validated")


def profile_schema_errors(repo_root: Path, *, schema_root: Path | None = None) -> list[str]:
    """Validate the project-root `openlakeforge.yaml` Deployment Profile
    against the canonical model and its versioned JSON Schema. Mirrors
    `descriptor_schema_errors`: the two validators run independently: neither
    substitutes for the other. `schema_root` points at the distribution
    payload's `docs/schema/` for an installed project (ADR 0009)."""
    from olf.profile import DeploymentProfileError, load_deployment_profile

    profile_path = repo_root / "openlakeforge.yaml"
    if not profile_path.is_file():
        return [f"no openlakeforge.yaml found under {repo_root}"]

    try:
        document = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f"openlakeforge.yaml: {exc}"]

    errors: list[str] = []
    try:
        load_deployment_profile(profile_path)
    except DeploymentProfileError as exc:
        errors.append(f"openlakeforge.yaml: canonical model rejected profile: {exc}")

    schema_path = (
        schema_root / "deployment-profile.schema.json"
        if schema_root is not None
        else repo_root / "docs/schema/deployment-profile.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    schema_errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    for error in schema_errors:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        errors.append(f"openlakeforge.yaml: schema violation at {location}: {error.message}")

    return errors


def _check_deployment_profile_schema_conformance(repo_root: Path, *, schema_root: Path | None = None) -> CheckResult:
    """The project-root `openlakeforge.yaml` must load via the canonical
    `olf.profile` model and conform to its versioned JSON Schema."""
    name = "deployment_profile_schema_conformance"
    errors = profile_schema_errors(repo_root, schema_root=schema_root)
    if errors:
        return CheckResult(name, ok=False, detail="; ".join(errors))
    return CheckResult(name, ok=True, detail="deployment profile validated")


def _check_hcl_structured_contracts(repo_root: Path) -> CheckResult:
    """Tier 1+2: parse each environment's `contracts.tf` and assert on the
    parsed tree -- required locals exist, required `check` blocks are
    declared, and Phase-2-forbidden fields are absent from the named local's
    own (possibly opaque) expression text. This is authoring-time guarding:
    nobody should write a forbidden key at all, regardless of whether it
    would ever evaluate truthy (see `_check_hcl_phase_two_invariants` for the
    tier that needs actually-resolved values)."""
    name = "hcl_structured_contracts"
    errors: list[str] = []
    environments_checked = 0

    for env in _ENVIRONMENT_ROOTS:
        contracts_path = repo_root / "infra/terraform/environments" / env / "contracts.tf"
        if not contracts_path.is_file():
            errors.append(f"{env}: missing contracts.tf at {contracts_path}")
            continue
        environments_checked += 1
        document = _parse_hcl(contracts_path)
        locals_map = _merged_locals(document)

        for required_local in _REQUIRED_CONTRACT_LOCALS:
            if required_local not in locals_map:
                errors.append(f"{env}/contracts.tf: missing required local {required_local!r}")

        declared_checks = {key for block in document.get("check", []) for key in block}
        for required_check in _REQUIRED_CONTRACT_CHECKS_BY_ENV.get(env, ()):
            if required_check not in declared_checks:
                errors.append(f"{env}/contracts.tf: missing required check block {required_check!r}")

        for local_name, value in locals_map.items():
            if not isinstance(value, str):
                continue
            for forbidden_field in _FORBIDDEN_PHASE_TWO_FIELDS:
                if f'"{forbidden_field}"' in value or f"{forbidden_field} " in value:
                    errors.append(
                        f"{env}/contracts.tf: local {local_name!r} references Phase-2-owned field "
                        f"{forbidden_field!r} (ADR 0002: namespaces/schemas are reconciled by "
                        f"`olf catalog sync-namespaces`, not declared in Terraform)"
                    )

        main_path = repo_root / "infra/terraform/environments" / env / "main.tf"
        if not main_path.is_file():
            errors.append(f"{env}: missing main.tf at {main_path}")
            continue
        main_document = _parse_hcl(main_path)
        main_locals = _merged_locals(main_document)
        if main_locals.get("catalog_namespace_model") != "medallion-owner":
            errors.append(f"{env}/main.tf: local.catalog_namespace_model must be 'medallion-owner'")
        for forbidden_field in _FORBIDDEN_PHASE_TWO_FIELDS:
            if forbidden_field in main_locals:
                errors.append(f"{env}/main.tf: locals must not declare Phase-2-owned field {forbidden_field!r}")
        for module_block in main_document.get("module", []):
            for module_name, module_body in module_block.items():
                if not isinstance(module_body, dict):
                    continue
                for forbidden_field in _FORBIDDEN_PHASE_TWO_FIELDS:
                    if forbidden_field in module_body:
                        errors.append(
                            f"{env}/main.tf: module {module_name!r} must not receive Phase-2-owned "
                            f"argument {forbidden_field!r}"
                        )

    glue_main_path = repo_root / "infra/terraform/modules/catalog/aws-glue/main.tf"
    if glue_main_path.is_file():
        glue_document = _parse_hcl(glue_main_path)
        glue_resources = {
            f"{resource_type}.{resource_name}"
            for resource_block in glue_document.get("resource", [])
            for resource_type, instances in resource_block.items()
            for resource_name in instances
        }
        if "aws_glue_catalog_database" in {rtype for rtype, _ in (r.split(".", 1) for r in glue_resources)}:
            errors.append(
                "infra/terraform/modules/catalog/aws-glue/main.tf: must not create aws_glue_catalog_database "
                "resources (ADR 0002: Phase 2 owns database lifecycle)"
            )
        removed_blocks = glue_document.get("removed", [])
        has_namespace_removal = any(
            block.get("from") == "${aws_glue_catalog_database.namespace}"
            and any(lc.get("destroy") is False for lc in block.get("lifecycle", []))
            for block in removed_blocks
        )
        if not has_namespace_removal:
            errors.append(
                "infra/terraform/modules/catalog/aws-glue/main.tf: missing a `removed { from = "
                "aws_glue_catalog_database.namespace, lifecycle { destroy = false } }` block handing "
                "existing databases to Phase 2 without destroying them"
            )
    else:
        errors.append(f"missing {glue_main_path}")

    if errors:
        return CheckResult(name, ok=False, detail="; ".join(errors))
    return CheckResult(name, ok=True, detail=f"{environments_checked} environment(s) validated")


_ENV_TO_PROVIDER = {"local": "local", "azure-poc": "azure", "aws-poc": "aws"}


def _installed_contract_environ(env: str) -> dict[str, str] | None:
    """Overlay `OPENLAKEFORGE_TERRAFORM_{STATE,DATA}_ROOT` for an installed
    distribution, matching `DeploymentContext.command_env()` exactly.

    `load_provider_contracts()` defaults to `os.environ`, which never has
    these vars outside an active `olf deploy` invocation. Without this, an
    installed distribution's `terraform output` reads the read-only
    payload's absent default state instead of what `olf deploy` wrote under
    `OLF_HOME`, and every environment is silently reported as unapplied
    (see `olf.tooling.terraform.external_state_options`). Returns `None`
    for a source checkout, where Terraform's own directory-relative default
    state is correct and `load_provider_contracts` already handles it.
    """
    import os

    from olf.deployment.context import DeploymentContext
    from olf.distribution import runtime_layout

    layout = runtime_layout()
    if layout.is_source:
        return None
    context = DeploymentContext.for_provider(
        _ENV_TO_PROVIDER[env],
        repo_root=layout.project_root,
        distribution_root=layout.distribution_root,
        state_root=layout.state_root,
        work_root=layout.work_root,
        cache_root=layout.cache_root,
    )
    return context.command_env(base=os.environ)


def _check_hcl_phase_two_invariants(repo_root: Path) -> CheckResult:
    """Tier 3: assert Phase-2-forbidden fields are absent from the *resolved*
    `provider_contracts.catalog` value, reusing the already-existing
    `contracts.load_provider_contracts()` (reads `terraform output -json` from
    applied state). Skips (does not fail) when no environment has been
    applied yet -- the normal case for a bare CI checkout -- matching
    `contracts.env`'s own fallback behavior."""
    name = "hcl_phase_two_invariants"
    errors: list[str] = []
    checked_environments: list[str] = []
    skipped_environments: list[str] = []

    for env in _ENVIRONMENT_ROOTS:
        terraform_dir = repo_root / "infra/terraform/environments" / env
        contracts = contracts_module.load_provider_contracts(
            str(terraform_dir), environ=_installed_contract_environ(env)
        )
        if contracts is None:
            skipped_environments.append(env)
            continue
        checked_environments.append(env)
        catalog = contracts.get("catalog")
        if not isinstance(catalog, dict):
            errors.append(f"{env}: applied provider_contracts is missing a 'catalog' entry")
            continue
        for forbidden_field in _FORBIDDEN_PHASE_TWO_FIELDS:
            if forbidden_field in catalog:
                errors.append(
                    f"{env}: applied provider_contracts.catalog resolves Phase-2-owned field "
                    f"{forbidden_field!r} (ADR 0002 violation)"
                )

    if errors:
        return CheckResult(name, ok=False, detail="; ".join(errors))
    if checked_environments:
        detail = f"{len(checked_environments)} applied environment(s) validated"
        if skipped_environments:
            detail += f"; skipped (no applied state): {', '.join(skipped_environments)}"
        return CheckResult(name, ok=True, detail=detail)
    return CheckResult(name, ok=True, detail="skipped: no applied Terraform state for any environment")


_AWS_GLUE_ENV_OVERRIDE = {
    "OPENLAKEFORGE_STORAGE_IMPLEMENTATION": "storage.aws_s3",
    "OPENLAKEFORGE_CATALOG_TYPE": "glue",
    "OPENLAKEFORGE_CATALOG_PROVIDER": "aws-glue",
    "OPENLAKEFORGE_CATALOG_GLUE_DATABASE": "sales_silver",
}


def _check_floe_rendered_profile() -> CheckResult:
    """Render the Floe profile in-process (no subprocess, no `olf floe
    render-profile` indirection) and assert on the *parsed* YAML, both for
    the default (local/REST) branch and the AWS-Glue-shaped env override."""
    name = "floe_rendered_profile"
    errors: list[str] = []

    local_parsed = yaml.safe_load(floe_module.render_profile({}))
    if local_parsed.get("catalogs", {}).get("default") != "iceberg_catalog":
        errors.append("local profile: catalogs.default must be 'iceberg_catalog'")
    local_definitions = local_parsed.get("catalogs", {}).get("definitions", [])
    if not local_definitions or local_definitions[0].get("type") != "rest":
        errors.append("local profile: catalogs.definitions[0].type must be 'rest'")

    aws_parsed = yaml.safe_load(floe_module.render_profile(_AWS_GLUE_ENV_OVERRIDE))
    aws_definitions = aws_parsed.get("catalogs", {}).get("definitions", [])
    if not aws_definitions or aws_definitions[0].get("type") != "glue":
        errors.append("aws-glue profile: catalogs.definitions[0].type must be 'glue'")
    if aws_definitions and "create_database_if_missing" not in aws_definitions[0]:
        errors.append("aws-glue profile: catalog definition must set create_database_if_missing")

    if errors:
        return CheckResult(name, ok=False, detail="; ".join(errors))
    return CheckResult(name, ok=True, detail="local (rest) and aws-glue profiles rendered and parsed")


def _check_floe_contract_structure(repo_root: Path) -> CheckResult:
    """Every `lakehouse_code/silver/*/contracts/floe/*.yml` must parse as YAML
    and use provider-neutral storage aliases and a domain-scoped Silver
    namespace matching `<domain>_silver` (never a shared "silver"/"gold"
    namespace, per ADR 0002)."""
    name = "floe_contract_structure"
    errors: list[str] = []
    contract_paths = sorted(repo_root.glob("lakehouse_code/silver/*/contracts/floe/*.yml"))
    if not contract_paths:
        return CheckResult(name, ok=False, detail="no lakehouse_code/silver/*/contracts/floe/*.yml files found")

    for contract_path in contract_paths:
        rel_path = contract_path.relative_to(repo_root)
        domain = contract_path.parents[2].name
        expected_namespace = f"{domain}_silver"
        document = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
        storage_names = {d.get("name") for d in document.get("storages", {}).get("definitions", [])}
        for required_alias in ("lakehouse_bronze", "lakehouse_silver"):
            if required_alias not in storage_names:
                errors.append(f"{rel_path}: storages.definitions must include {required_alias!r}")
        for entity in document.get("entities", []):
            sink_iceberg = entity.get("sink", {}).get("accepted", {}).get("iceberg", {})
            namespace = sink_iceberg.get("namespace")
            if sink_iceberg.get("catalog") == "polaris":
                errors.append(
                    f"{rel_path}: entity {entity.get('name')!r} sink.accepted.iceberg.catalog must not "
                    "name the 'polaris' provider directly (use the logical 'iceberg_catalog' alias)"
                )
            if namespace != expected_namespace:
                errors.append(
                    f"{rel_path}: entity {entity.get('name')!r} sink.accepted.iceberg.namespace "
                    f"must be domain-scoped {expected_namespace!r}, got {namespace!r}"
                )

    if errors:
        return CheckResult(name, ok=False, detail="; ".join(errors))
    return CheckResult(name, ok=True, detail=f"{len(contract_paths)} Floe contract file(s) validated")


def _check_floe_profile_templates(repo_root: Path) -> CheckResult:
    """Checked-in Floe runtime profile templates must parse as YAML and
    declare the top-level shape the Floe runner expects."""
    name = "floe_profile_templates"
    errors: list[str] = []
    required_keys = ("apiVersion", "kind", "metadata", "variables", "catalogs", "execution", "validation")
    profile_paths = sorted(repo_root.glob("libs/floe/profiles/*.yml"))
    if not profile_paths:
        return CheckResult(name, ok=False, detail="no libs/floe/profiles/*.yml files found")

    for profile_path in profile_paths:
        rel_path = profile_path.relative_to(repo_root)
        document = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
        for required_key in required_keys:
            if required_key not in document:
                errors.append(f"{rel_path}: missing required top-level key {required_key!r}")

    if errors:
        return CheckResult(name, ok=False, detail="; ".join(errors))
    return CheckResult(name, ok=True, detail=f"{len(profile_paths)} Floe profile template(s) validated")


def _check_helm_values_as_data(repo_root: Path) -> CheckResult:
    """Parse the static Dagster Helm values input as data. Does not invoke
    `helm template` -- that remains `check-infra.sh`'s job."""
    name = "helm_values_as_data"
    values_path = repo_root / "infra/helm/values/local/dagster.yaml"
    if not values_path.is_file():
        return CheckResult(name, ok=False, detail=f"missing {values_path}")

    document = yaml.safe_load(values_path.read_text(encoding="utf-8"))
    compute_log_manager = document.get("computeLogManager", {})
    errors: list[str] = []
    if compute_log_manager.get("type") != "S3ComputeLogManager":
        errors.append("computeLogManager.type must be 'S3ComputeLogManager'")
    s3_config = compute_log_manager.get("config", {}).get("s3ComputeLogManager", {})
    if not s3_config.get("bucket"):
        errors.append("computeLogManager.config.s3ComputeLogManager.bucket must be set")

    if errors:
        return CheckResult(name, ok=False, detail="; ".join(errors))
    return CheckResult(name, ok=True, detail="infra/helm/values/local/dagster.yaml validated as data")


# Makefile targets whose recipe bodies must resolve real, non-empty kubeconfig
# and context inputs. `make -n <target>` prints the fully variable-expanded
# recipe, so this catches a variable-name typo that would still `grep` as
# present in the unexpanded Makefile source but resolve to empty/wrong at
# expansion time.
#
# `local-forward`/`azure-forward`/`aws-forward` are deliberately absent:
# since #124 (local) and #125 (AWS/Azure) they delegate to
# `olf forward --provider <provider>`, which resolves KUBECONFIG/kube-context
# internally - local through `DeploymentContext.local()`, cloud through
# `CloudProvider._foundation_facts` reading the foundation's Terraform
# outputs - rather than embedding them in the Make recipe text; that wiring
# is covered by `tools/olf/tests/test_deployment_context.py`,
# `tools/olf/tests/test_local_forward.py`, `tools/olf/tests/
# test_cloud_provider.py`, `tools/olf/tests/test_cloud_aws.py`,
# `tools/olf/tests/test_cloud_azure.py`, and `tools/olf/tests/
# test_cli_deployment.py` instead.
_KUBE_WIRED_TARGETS = (
    "floe-manifest-upload",
    "superset-reports-deploy",
    "superset-reports-export",
    "openmetadata-metadata-deploy",
)

_KUBECONFIG_PATTERN = re.compile(r'KUBECONFIG(?:_PATH)?="([^"]*)"')
_KUBE_CONTEXT_PATTERN = re.compile(r'(?:KUBE_CONTEXT=|context=")([^\s"]+)"?')
_KUBECONFIG_ARGUMENT_PATTERN = re.compile(r"--kubeconfig-path\s+(\S+)")
_KUBE_CONTEXT_ARGUMENT_PATTERN = re.compile(r"--(?:kube-context|cluster-name)\s+(\S+)")


def _check_makefile_target_wiring(repo_root: Path) -> CheckResult:
    """Validate each Kubernetes-touching Make delegate's expanded runtime inputs.

    The `Makefile` is deliberately excluded from the distribution payload as
    deprecated checkout compatibility (ADR 0008), so its absence here is
    expected for an installed project and is not a failure."""
    name = "makefile_target_wiring"
    if not (repo_root / "Makefile").is_file():
        return CheckResult(name, ok=True, detail=f"skipped: no Makefile under {repo_root}")
    errors: list[str] = []
    checked = 0

    for target in _KUBE_WIRED_TARGETS:
        try:
            result = subprocess.run(
                ["make", "-n", target],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            errors.append(f"{target}: failed to invoke make: {exc}")
            continue
        if result.returncode != 0:
            errors.append(f"{target}: `make -n {target}` failed: {result.stderr.strip()}")
            continue
        checked += 1
        output = result.stdout

        kubeconfig_match = _KUBECONFIG_PATTERN.search(output) or _KUBECONFIG_ARGUMENT_PATTERN.search(output)
        if not kubeconfig_match or not kubeconfig_match.group(1):
            errors.append(f"{target}: recipe does not resolve a non-empty KUBECONFIG(_PATH) value")

        context_match = _KUBE_CONTEXT_PATTERN.search(output) or _KUBE_CONTEXT_ARGUMENT_PATTERN.search(output)
        if not context_match or not context_match.group(1):
            errors.append(f"{target}: recipe does not resolve a non-empty kube context value")

    if errors:
        return CheckResult(name, ok=False, detail="; ".join(errors))
    return CheckResult(name, ok=True, detail=f"{checked} target(s) dry-run and validated")


def run_contracts_check(
    repo_root: str | Path = ".", *, distribution_root: str | Path | None = None
) -> ContractsCheckReport:
    """Run every behavioral contract check against `repo_root`.

    `distribution_root` is where the immutable platform payload lives —
    `infra/terraform`, `infra/helm`, `libs/floe/profiles`, `docs/schema` — and
    defaults to `repo_root` for a source checkout, where the two coincide. An
    installed project's `repo_root` has only `lakehouse_code/` (ADR 0009), so
    every payload-owned check below runs against `distribution_root` instead;
    `lakehouse_code/silver/*/contracts/floe/*.yml` remains project-owned and
    always reads from `repo_root`. `_check_makefile_target_wiring` skips
    rather than fails when there is no `Makefile` to invoke -- the Makefile is
    deliberately excluded from the distribution payload as deprecated
    checkout compatibility (ADR 0008).
    """
    root = Path(repo_root).resolve()
    if not root.is_dir():
        raise ValueError(f"repo root does not exist: {root}")
    dist_root = Path(distribution_root).resolve() if distribution_root is not None else root

    report = ContractsCheckReport()
    report.results.append(_check_descriptor_schema_conformance(root, schema_root=dist_root / "docs" / "schema"))
    report.results.append(_check_deployment_profile_schema_conformance(root, schema_root=dist_root / "docs" / "schema"))
    report.results.append(_check_hcl_structured_contracts(dist_root))
    report.results.append(_check_hcl_phase_two_invariants(dist_root))
    report.results.append(_check_floe_rendered_profile())
    report.results.append(_check_floe_contract_structure(root))
    report.results.append(_check_floe_profile_templates(dist_root))
    report.results.append(_check_helm_values_as_data(dist_root))
    report.results.append(_check_makefile_target_wiring(dist_root))
    return report
