"""The contract every provider adapter must satisfy, asserted once per provider.

The per-module tests next to this one each cover one adapter. Nothing before
this suite asserted that *every* adapter satisfies the same contract, so an
adapter could diverge and only fail at runtime, on the provider nobody
exercises locally (issue #188).

The parameter list is `olf.deployment.context.Provider` itself rather than a
literal list, so a provider is enrolled the moment the enum names it: adding
one fails here — with a message naming the contract it broke — until its
`DeploymentContext` factory, its `build_provider` branch, its captured
contract fixture, and its Terraform root all exist.

Runs with no cluster and no cloud credentials: every provider is built from an
empty environment, every subprocess goes to a recording runner, and the
provider contract each root emits is read from the captured fixture beside
this file. What that leaves uncovered is one thing only — that a real
`terraform apply` of a root emits exactly the fixture's payload. That needs
Terraform and a provider account, and stays with `olf e2e run`. The two static
checks here (the root declares a v3 stage-indexed contract surface, and the
outputs shared tooling reads) are what catch a root drifting away from its
fixture in the meantime.

Assertions are written against what a caller can observe. The destructive-apply
guards are driven through each provider's platform apply rather than called
directly, so a provider whose apply stops invoking them fails here; the
Protocol check binds Protocol-shaped calls rather than comparing signature
text, so an adapter may be rewritten freely as long as shared callers still
work; and cross-provider equality covers only the logical identities olf itself
derives, never physical names the provider contract owns.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import hcl2
import pytest
from _cloud_support import FakeCloudBackend
from _tooling_support import RecordedCall, RecordingRunner
from conftest import write_two_product_fixture

from olf.deployment.cloud.backend import FoundationFacts
from olf.deployment.context import DeploymentContext, Provider, stage_namespace
from olf.deployment.engine import (
    DeploymentEngine,
    DeploymentPhase,
    DeploymentProvider,
    Toolkit,
    build_provider,
)
from olf.deployment.errors import DeploymentPreconditionError
from olf.profile import StageName, resolve_topology, validate_deployment_profile
from olf.provider_contracts import ProviderContractError, parse_provider_contracts
from olf.tooling.aws import AwsSdk
from olf.tooling.azure import AzureSdk
from olf.tooling.docker import Docker
from olf.tooling.helm import Helm
from olf.tooling.kind import Kind
from olf.tooling.kubectl import Kubectl
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver
from olf.tooling.terraform import Terraform

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).parent / "fixtures"

every_provider = pytest.mark.parametrize("provider", tuple(Provider), ids=[p.value for p in Provider])

_FAKE_TOOLS = ("terraform", "docker", "kind", "kubectl", "helm", "aws", "az")

# What a cloud foundation reports once applied. Only the cloud platform
# apply reads it; the local one derives its context statically.
_FOUNDATION_FACTS = FoundationFacts(
    cluster_name="olf-conformance",
    kube_context="olf-conformance",
    project_code_repository="registry.example/project-code",
    superset_repository="registry.example/superset",
)

# `DeploymentProvider`'s own members, read off the Protocol rather than
# restated: a member added there has to be implemented by every adapter, and
# this suite is where that becomes true rather than a review comment.
_PROTOCOL_ATTRIBUTES = tuple(sorted(DeploymentProvider.__annotations__))
_PROTOCOL_METHODS = tuple(
    sorted(
        name
        for name, value in vars(DeploymentProvider).items()
        if inspect.isfunction(value) and not name.startswith("__")
    )
)

# ADR 0002's three phases, in the order `olf deploy` must run them, with the
# adapter step each one dispatches to. PREFETCH is olf's own pre-platform
# image step rather than an ADR phase; it sits here because it is part of the
# order every adapter is driven in (it is deliberately inert on cloud).
_ADR_0002_PHASES = (DeploymentPhase.FOUNDATION, DeploymentPhase.PLATFORM, DeploymentPhase.ARTIFACTS)
_PHASE_STEP = {
    DeploymentPhase.FOUNDATION: "foundation_up",
    DeploymentPhase.PREFETCH: "prepare_images",
    DeploymentPhase.PLATFORM: "platform_up",
    DeploymentPhase.ARTIFACTS: "artifacts_deploy",
}

# Terraform outputs shared, provider-neutral tooling reads by name from every
# platform root. `stage_names` is the load-bearing one: a root that does not
# declare it makes `terraform output` answer "output variable requested could
# not be found", which `applied_stage_names` reads as "nothing applied yet" -
# silently disarming the stage-removal guard on that provider.
_REQUIRED_ROOT_OUTPUTS = ("provider_contracts", "shared_namespace", "stage_names")

_MEDALLION_LAYERS = ("bronze", "silver", "gold")

# The stages every provider's captured contract must serve, so cross-provider
# comparison is over a declared set rather than whatever the fixtures happen to
# share - an intersection would skip a dropped stage instead of failing on it.
# A provider may serve more (AWS's fixture also carries UAT); it may not serve
# fewer.
_CONFORMANCE_STAGES = (StageName.DEV, StageName.PROD)


def _ok(stdout: str = "") -> CommandResult:
    return CommandResult(argv=(), returncode=0, stdout=stdout, stderr="", duration_seconds=0.0)


def _fail() -> CommandResult:
    return CommandResult(argv=(), returncode=1, stdout="", stderr="", duration_seconds=0.0)


def _toolkit(runner: RecordingRunner) -> Toolkit:
    resolver = PathExecutableResolver(overrides={name: Path(name) for name in _FAKE_TOOLS})
    return Toolkit(
        runner=runner,
        resolver=resolver,
        terraform=Terraform(runner, resolver),
        helm=Helm(runner, resolver),
        kubectl=Kubectl(runner, resolver),
        docker=Docker(runner, resolver),
        kind=Kind(runner, resolver),
        aws=AwsSdk(),
        azure=AzureSdk(),
    )


def _permitted_calls(protocol_method: Any) -> tuple[tuple[tuple[Any, ...], dict[str, Any]], ...]:
    """The (args, kwargs) shapes a caller holding only the Protocol may use.

    Two shapes per method - every optional argument omitted, and every one
    supplied - which is the whole space for this Protocol (no *args/**kwargs,
    no overloads).
    """
    marker = object()
    parameters = list(inspect.signature(protocol_method).parameters.values())[1:]  # drop `self`
    args: list[Any] = []
    required: dict[str, Any] = {}
    optional: dict[str, Any] = {}
    for parameter in parameters:
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        if parameter.default is not parameter.empty:
            optional[parameter.name] = marker
        elif parameter.kind is parameter.KEYWORD_ONLY:
            required[parameter.name] = marker
        else:
            args.append(marker)
    return ((tuple(args), dict(required)), (tuple(args), {**required, **optional}))


def _calls_the_adapter_rejects(protocol_method: Any, implementation: Any) -> list[str]:
    """Protocol-shaped calls the bound implementation would not accept.

    Binds rather than compares signature text: an adapter may spell an
    annotation differently, narrow a return type, or add an argument of its own
    with a default, and none of that changes what a shared caller can do. Only
    a call the Protocol permits and the adapter refuses is a contract break.
    """
    rejected = []
    for args, kwargs in _permitted_calls(protocol_method):
        try:
            inspect.signature(implementation).bind(*args, **kwargs)
        except TypeError:
            rendered = ", ".join(["<positional>"] * len(args) + [f"{name}=..." for name in kwargs])
            rejected.append(f"{protocol_method.__name__}({rendered})")
    return rejected


def _context(provider: Provider, repo_root: Path, **kwargs: Any) -> DeploymentContext:
    factory = getattr(DeploymentContext, provider.value, None)
    assert factory is not None, (
        f"{provider.value!r} has no DeploymentContext.{provider.value}() factory. Every provider needs one: "
        "it is how shared code resolves that provider's paths, kube context, and scoped command environment "
        "without knowing which provider it is running against."
    )
    return factory(repo_root=repo_root, **kwargs)


def _adapter(provider: Provider, repo_root: Path, runner: RecordingRunner | None = None, **kwargs: Any):  # noqa: ANN202
    """The provider's adapter, built the way `olf deploy` builds it."""
    context = _context(provider, repo_root, **kwargs)
    return build_provider(context, toolkit=_toolkit(runner or RecordingRunner()), environ={})


def _contract(provider: Provider) -> dict[str, Any]:
    path = FIXTURES / f"{provider.value}-provider-contracts-v3.json"
    assert path.is_file(), (
        f"{provider.value!r} has no captured provider contract at {path.name}. Every provider must publish one: "
        "capture its root's `terraform output -json provider_contracts` so this suite can assert the contract "
        "olf parses at runtime, rather than trusting the adapter."
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _topology_of(contract: dict[str, Any]):  # noqa: ANN202 - DeploymentTopology
    """The Deployment Profile that contract claims to serve, resolved."""
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
                    "stages": {
                        name: {
                            "enabled": True,
                            "capabilities": {
                                "analytics": "reporting" in stage,
                                "governance": "governance" in stage,
                            },
                        }
                        for name, stage in contract["stages"].items()
                    },
                },
            }
        )
    )


def _single_stage_topology(provider: Provider, *, enabled: tuple[StageName, ...]):  # noqa: ANN202
    deployment = _contract(provider)["deployment"]
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
                    "stages": {stage.value: {"enabled": True} for stage in enabled},
                },
            }
        )
    )


def _root_locals(terraform_root: Path) -> dict[str, Any]:
    """Every `locals { }` block in the root's contract surface, flattened.

    Values built from `merge(...)`/`for` comprehensions collapse to one opaque
    `${...}` expression string: `hcl2` parses HCL syntax, not Terraform
    semantics. Callers that need those scope their assertion to that string.
    """
    document = hcl2.loads((terraform_root / "contracts.tf").read_text(encoding="utf-8"))
    merged: dict[str, Any] = {}
    for block in document.get("locals", []):
        merged.update(block)
    return merged


def _logical_identities(stage_name: StageName, stage: Any) -> tuple[tuple[str, str, str], ...]:
    """The names shared, provider-neutral code derives from the stage alone.

    Each has exactly one correct value per stage, on every provider, because
    olf itself computes the expected one. Physical object-store and catalog
    naming is *not* here: rule 2 delegates it to the provider contract, so an
    account-derived bucket name is correct rather than a violation.
    """
    return (
        ("namespace", str(stage.namespace), stage_namespace(stage_name)),
        ("catalog.catalog_name", str(stage.catalog["catalog_name"]), f"lakehouse_{stage_name.value}"),
        ("query.catalog_name", str(stage.query["catalog_name"]), f"lakehouse_{stage_name.value}"),
        ("activation.prefix", str(stage.activation["prefix"]), f"activations/{stage_name.value}"),
    )


def _guard_runner(
    *,
    applied_stages: str | None = None,
    legacy_namespace_exists: bool = False,
    labelled_namespaces: str = "",
) -> RecordingRunner:
    """A runner standing in for the cluster a platform apply queries on its way
    to the destructive-apply guards: a reachable kube context, cached charts,
    `terraform output -json stage_names`, the pre-v0.3 shared namespace, and the
    namespaces labelled as this deployment's."""

    class _Runner(RecordingRunner):
        def run(self, command, **kwargs):  # type: ignore[override]  # noqa: ANN001, ANN202
            argv = list(command.argv) if hasattr(command, "argv") else [str(part) for part in command]
            self.calls.append(RecordedCall(argv=argv, kwargs=kwargs))
            if "stage_names" in argv:
                return _ok(applied_stages if applied_stages is not None else "[]")
            if argv[0] == "kubectl" and "get-contexts" in argv:
                return _ok("kind-openlakeforge-local\n")
            if "get" in argv and "namespace" in argv and "lakehouse" in argv:
                return _ok() if legacy_namespace_exists else _fail()
            if "namespace" in argv and "-l" in argv:
                return _ok(labelled_namespaces)
            if argv[0] == "kubectl" and "namespace" in argv and "get" in argv:
                return _fail()
            # A cache hit, so a platform apply reaching the guards never pulls
            # or repacks a real chart.
            if argv[0] == "helm" and argv[1:3] == ["show", "chart"]:
                return _ok()
            return _ok()

    return _Runner()


def _ready_for_platform_apply(config: Any) -> None:
    """The preconditions every provider's platform apply checks before it
    reaches the guards: an applied foundation, and a cached chart per release."""
    config.paths.foundation_state_path.parent.mkdir(parents=True, exist_ok=True)
    config.paths.foundation_state_path.write_text("{}", encoding="utf-8")
    config.paths.helm_cache_dir.mkdir(parents=True, exist_ok=True)
    for setting in config.charts.values():
        if setting.package_path is not None:
            Path(setting.package_path).parent.mkdir(parents=True, exist_ok=True)
            Path(setting.package_path).write_text("cached", encoding="utf-8")


def _platform_apply(provider: Provider, adapter: Any) -> None:
    """Run this provider's platform apply, the call path the guards live on.

    Dispatched per provider family rather than through `adapter.platform_up()`
    because the cloud provider resolves live foundation facts first (a real
    Terraform state and a reachable cluster) - `FakeCloudBackend` stands in for
    that, and nothing else about the apply is stubbed. Calling the guards
    directly instead would leave this suite green if a provider's apply stopped
    calling them, which is the regression that matters.
    """
    if provider is Provider.LOCAL:
        from olf.deployment.local import platform as local_platform

        local_platform.platform_up(adapter.config, adapter.tools, env={})
        return
    if provider in (Provider.AWS, Provider.AZURE):
        from olf.deployment.cloud import platform as cloud_platform

        cloud_platform.platform_up(
            adapter.config,
            adapter.tools,
            FakeCloudBackend(scope=provider.value),
            _FOUNDATION_FACTS,
            env={},
        )
        return
    raise AssertionError(
        f"{provider.value!r} has no platform apply wired into this suite. Every provider applies its platform "
        f"through one entry point, and the destructive-apply guards live on it - a provider whose apply this "
        f"suite cannot reach is a provider whose guards are unverified."
    )


@every_provider
def test_adapter_satisfies_the_deployment_provider_protocol(provider: Provider, tmp_path: Path) -> None:
    """`build_provider` returns something that structurally *is* a
    `DeploymentProvider` - the seam architectural rule 1 makes shared code go
    through. Signatures are compared exactly: a step that quietly took an
    extra provider-specific argument would type-check at its own call site and
    break the shared engine."""
    adapter = _adapter(provider, tmp_path)

    for attribute in _PROTOCOL_ATTRIBUTES:
        assert hasattr(adapter, attribute), (
            f"{type(adapter).__name__} has no {attribute!r}. DeploymentProvider declares it, so shared code "
            f"reads it on every provider; without it that code has to reach past the Protocol to keep working."
        )
    for method in _PROTOCOL_METHODS:
        implementation = getattr(adapter, method, None)
        assert callable(implementation), (
            f"{type(adapter).__name__} does not implement {method}(). Every DeploymentProvider member is a "
            f"lifecycle step `olf deploy`/`olf destroy` or a Make target delegate calls directly."
        )
        rejected = _calls_the_adapter_rejects(getattr(DeploymentProvider, method), implementation)
        assert not rejected, (
            f"{type(adapter).__name__}.{method}() rejects a call DeploymentProvider permits: {rejected!r}. "
            f"Shared code calls every adapter the same way, so an adapter may add optional arguments but may "
            f"not require its own or drop one the Protocol declares."
        )


@every_provider
def test_adapter_exposes_the_command_environment_shared_callers_use(provider: Provider, tmp_path: Path) -> None:
    """`olf.deployment.activation` reads `provider.env` on whichever provider
    it is handed. It is not a `DeploymentProvider` member yet, which is why
    those reads carry `# type: ignore[attr-defined]` (#187 Group A) - asserted
    here on what every adapter actually exposes, so a new adapter that omits
    it fails now rather than at activation time.

    Read off the class: `env` resolves the Docker engine endpoint (and, on
    cloud, the foundation's Terraform outputs) the first time it is touched.
    """
    adapter = _adapter(provider, tmp_path)

    assert hasattr(type(adapter), "env"), (
        f"{type(adapter).__name__} has no `env`. Stage activation drives Helm and Docker through the provider's "
        f"own command-environment overlay; without it, activation cannot run against this provider."
    )


@every_provider
def test_platform_root_declares_a_v3_stage_indexed_contract_surface(provider: Provider) -> None:
    """The root emits one contract keyed by stage, not one contract per apply.

    Deliberately shape-agnostic about the bindings themselves (those are
    `parse_provider_contracts`' job, asserted from the captured output below):
    what is checked here is that the surface is a v3 index built over the
    enabled stages, with every stage-scoped reference pinned to the stage
    being emitted rather than to a fixed one.
    """
    terraform_root = _context(provider, REPO_ROOT).paths.platform_terraform_dir
    assert (terraform_root / "contracts.tf").is_file(), (
        f"{provider.value!r} has no contract surface at {terraform_root}/contracts.tf. Every provider's platform "
        f"root declares one: it is the only thing olf reads a deployment's physical values from."
    )
    contract_surface = _root_locals(terraform_root).get("provider_contracts")

    assert contract_surface is not None, (
        f"{terraform_root}/contracts.tf declares no `provider_contracts` local. It is the single value the root "
        f"publishes to olf; a root without it has no contract at all."
    )
    assert contract_surface.get("schema_version") == "3.0.0", (
        f"{terraform_root}/contracts.tf emits provider-contract schema "
        f"{contract_surface.get('schema_version')!r}. Every root emits v3: v2 is a compatibility adaptation for "
        f"already-applied deployments, not something a root may still be written against."
    )
    stages = str(contract_surface.get("stages", ""))
    assert "local.enabled_stages" in stages, (
        f"{terraform_root}/contracts.tf does not build `provider_contracts.stages` over `local.enabled_stages`. "
        f"The contract is a stage index: every enabled stage gets its own bindings from the resolved topology, "
        f"and no stage may be hardcoded."
    )
    for reference in (
        "stage/${name}/runtime_identity",
        "stage/${name}/catalog",
        "stage/${name}/orchestration",
        "activations/${name}",
        "shared/ops_storage",
    ):
        assert reference in stages, (
            f"{terraform_root}/contracts.tf never emits {reference!r} in its stage index. Stage-scoped bindings "
            f"must be pinned to the stage being emitted (and shared ones to `shared/...`); "
            f"`parse_provider_contracts` rejects any other value, so a root that hardcodes one stage's name "
            f"fails at deploy time on the second stage."
        )


@every_provider
def test_platform_root_declares_the_outputs_shared_tooling_reads(provider: Provider) -> None:
    """Missing `stage_names` does not fail loudly: `terraform output` answers
    "output variable requested could not be found", which `applied_stage_names`
    reads as an unapplied root - so the stage-removal guard would wave through
    the very apply it exists to stop, on that provider only."""
    terraform_root = _context(provider, REPO_ROOT).paths.platform_terraform_dir
    document = hcl2.loads((terraform_root / "outputs.tf").read_text(encoding="utf-8"))
    declared = {name for block in document.get("output", []) for name in block}

    for output in _REQUIRED_ROOT_OUTPUTS:
        assert output in declared, (
            f"{terraform_root}/outputs.tf does not declare the {output!r} output. Provider-neutral olf code reads "
            f"it by name on every provider, and a missing Terraform output reads as an empty one rather than as "
            f"an error."
        )


@every_provider
def test_every_enabled_stage_resolves_from_the_contract(provider: Provider) -> None:
    """The contract the root emits parses, and serves every enabled stage."""
    contract = _contract(provider)
    topology = _topology_of(contract)

    parsed = parse_provider_contracts(contract, topology)

    enabled = {stage.name for stage in topology.stages if stage.enabled}
    assert set(parsed.stages) == enabled, (
        f"{provider.value}'s contract serves {sorted(stage.value for stage in parsed.stages)!r} but its topology "
        f"enables {sorted(stage.value for stage in enabled)!r}. A stage the profile enables and the contract "
        f"cannot serve is a stage whose deploy fails after the platform is already applied."
    )
    for stage in enabled:
        assert parsed.for_stage(stage).name == stage, (
            f"{provider.value}'s contract does not resolve the {stage.value!r} stage by name."
        )
    absent = next((stage for stage in StageName if stage not in enabled), None)
    if absent is not None:
        with pytest.raises(ProviderContractError, match=absent.value):
            parsed.for_stage(absent)


@every_provider
def test_logical_stage_identities_derive_from_stage_identity_alone(provider: Provider) -> None:
    """AGENTS.md architectural rule 2, on the names the rule actually governs.

    Namespace, SQL catalog name, and activation prefix are computed by shared
    provider-neutral code from the stage name, so each has exactly one correct
    value on every provider. Physical object-store and catalog naming is
    explicitly delegated to the provider contract and is deliberately not
    pinned here - see the shape assertions below, and
    `test_logical_stage_identities_are_identical_across_providers`.
    """
    contract = _contract(provider)
    parsed = parse_provider_contracts(contract, _topology_of(contract))

    for name, stage in parsed.stages.items():
        for field, actual, canonical in _logical_identities(name, stage):
            assert actual == canonical, (
                f"{provider.value}'s {name.value!r} stage names its {field} {actual!r}, not the canonical "
                f"{canonical!r}. Shared code derives this name from the stage alone, so a provider that spells "
                f"it differently is one shared code cannot address."
            )


@every_provider
def test_physical_stage_storage_stays_isolated_per_stage(provider: Provider) -> None:
    """The provider contract owns physical bucket naming (rule 2 delegates it),
    so this asserts the property stage isolation depends on rather than the
    spelling: each layer's bucket is distinct, and belongs to exactly one stage
    and one layer."""
    contract = _contract(provider)
    parsed = parse_provider_contracts(contract, _topology_of(contract))

    seen: dict[str, str] = {}
    for name, stage in parsed.stages.items():
        for layer in _MEDALLION_LAYERS:
            bucket = str(stage.storage[layer]["bucket_name"])
            owner = f"{name.value}/{layer}"
            assert bucket not in seen, (
                f"{provider.value} gives {owner} the same bucket as {seen[bucket]}: {bucket!r}. Stage data-plane "
                f"isolation is what keeps DEV out of PROD's data; two stages sharing a bucket removes it."
            )
            seen[bucket] = owner


def test_logical_stage_identities_are_identical_across_providers() -> None:
    """The same profile, deployed to a different provider, addresses a stage by
    the same logical names. Deliberately limited to the logical identities:
    physical bucket names and runtime principals may legitimately be
    account-derived, and rule 2 delegates them to the provider contract.

    The stages compared are declared here rather than intersected from the
    fixtures, so a captured contract that quietly drops one fails instead of
    being skipped.
    """
    parsed = {}
    for provider in Provider:
        contract = _contract(provider)
        parsed[provider] = parse_provider_contracts(contract, _topology_of(contract))
    profiles = {provider.value: contracts.deployment["profile_name"] for provider, contracts in parsed.items()}
    assert len(set(profiles.values())) == 1, (
        f"the captured contracts describe different Deployment Profiles ({profiles!r}), so nothing here compares "
        f"like with like. Capture every provider's contract from the same profile."
    )
    for provider, contracts in parsed.items():
        missing = sorted(stage.value for stage in _CONFORMANCE_STAGES if stage not in contracts.stages)
        assert not missing, (
            f"{provider.value}'s captured contract does not serve {missing!r}. Every provider must serve the "
            f"same baseline stages for this suite to compare them; a provider missing one is the omission the "
            f"suite exists to catch, not a case to skip."
        )

    for stage in _CONFORMANCE_STAGES:
        identities = {
            provider.value: tuple(
                actual for _, actual, _ in _logical_identities(stage, contracts.for_stage(stage))
            )
            for provider, contracts in parsed.items()
        }
        assert len(set(identities.values())) == 1, (
            f"the {stage.value!r} stage is addressed by different logical names per provider: {identities!r}. "
            f"These are derived from the stage alone, so a difference here means shared code has to know which "
            f"provider it is talking to."
        )


def _apply_ready_adapter(provider: Provider, tmp_path: Path, runner: RecordingRunner, **kwargs: Any):  # noqa: ANN202
    """An adapter whose platform apply can run as far as the guards."""
    adapter = _adapter(
        provider,
        tmp_path,
        runner=runner,
        topology=_single_stage_topology(provider, enabled=(StageName.DEV,)),
        **kwargs,
    )
    _ready_for_platform_apply(adapter.config)
    return adapter


@every_provider
def test_a_platform_apply_refuses_to_remove_an_applied_stage(provider: Provider, tmp_path: Path) -> None:
    """Driven through the provider's platform apply, not by calling the guard:
    a provider whose apply stops calling `require_no_stage_removal` has to fail
    here. What it protects is an apply that is already deleting a namespace,
    its services, and their credentials."""
    adapter = _apply_ready_adapter(provider, tmp_path, _guard_runner(applied_stages='["dev", "prod"]'))

    with pytest.raises(DeploymentPreconditionError, match="prod"):
        _platform_apply(provider, adapter)


@every_provider
def test_a_platform_apply_refuses_a_stage_dropped_only_from_the_cluster(provider: Provider, tmp_path: Path) -> None:
    """The drift path: Terraform state is gone, so `stage_names` reports
    nothing applied, and the namespaces still labelled as this deployment's are
    the only signal left that a dropped stage is deployed."""
    adapter = _apply_ready_adapter(
        provider, tmp_path, _guard_runner(labelled_namespaces="olf-system\nolf-dev\nolf-prod\n")
    )

    with pytest.raises(DeploymentPreconditionError, match="prod"):
        _platform_apply(provider, adapter)


@every_provider
def test_a_platform_apply_proceeds_past_removal_under_the_explicit_opt_in(
    provider: Provider, tmp_path: Path
) -> None:
    """`--allow-stage-removal` is the one thing that lets the apply through,
    and it means the same on every provider."""
    adapter = _apply_ready_adapter(
        provider, tmp_path, _guard_runner(applied_stages='["dev", "prod"]'), allow_stage_removal=True
    )

    _platform_apply(provider, adapter)


@every_provider
def test_a_platform_apply_refuses_to_replace_the_pre_v0_3_shared_namespace(
    provider: Provider, tmp_path: Path
) -> None:
    """Every root's shared-services namespace was `lakehouse` before the
    stage-aware rewrite. A namespace name is immutable, so an apply against an
    unmigrated cluster plans to destroy it - and the SeaweedFS, PostgreSQL, and
    Polaris state inside it - to create an empty `olf-system`."""
    adapter = _apply_ready_adapter(provider, tmp_path, _guard_runner(legacy_namespace_exists=True))

    with pytest.raises(DeploymentPreconditionError, match="lakehouse"):
        _platform_apply(provider, adapter)


@every_provider
def test_a_platform_apply_proceeds_on_a_fresh_cluster(provider: Provider, tmp_path: Path) -> None:
    """Neither guard fires where there is nothing deployed to destroy."""
    adapter = _apply_ready_adapter(provider, tmp_path, _guard_runner())

    _platform_apply(provider, adapter)


def _record_steps(adapter_class: type, monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    for step in _PHASE_STEP.values():
        assert hasattr(adapter_class, step), (
            f"{adapter_class.__name__} does not implement {step}(). `olf deploy --phase` selects it by name on "
            f"every provider; a provider with nothing to do there implements it as a no-op rather than omitting it."
        )

        def _recorder(self, *args: Any, step: str = step, **kwargs: Any) -> None:  # noqa: ANN001, ARG001
            calls.append(step)

        monkeypatch.setattr(adapter_class, step, _recorder)


@every_provider
def test_every_deploy_phase_selects_its_own_adapter_step(
    provider: Provider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0002: each phase is selectable with `--phase`, and selecting one
    runs that phase alone - a platform apply that also ran an artifact step
    would put user code inside the static lifecycle."""
    write_two_product_fixture(tmp_path, dashboards=(("widgets_overview", "widgets_alpha"),))
    adapter = _adapter(provider, tmp_path)
    calls: list[str] = []
    _record_steps(type(adapter), monkeypatch, calls)
    engine = DeploymentEngine(adapter)

    for phase, step in _PHASE_STEP.items():
        calls.clear()
        engine.deploy(phase)

        assert calls == [step], (
            f"`olf deploy --provider {provider.value} --phase {phase.value}` ran {calls!r} rather than {[step]!r}. "
            f"Each phase runs its own step and nothing else."
        )


@every_provider
def test_deploying_everything_runs_the_adr_0002_order(
    provider: Provider, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """foundation -> platform -> artifacts, identically on every provider.
    Static infrastructure is applied before anything derived from
    `lakehouse_code/` exists, which is what keeps a code commit off Terraform."""
    write_two_product_fixture(tmp_path, dashboards=(("widgets_overview", "widgets_alpha"),))
    adapter = _adapter(provider, tmp_path)
    calls: list[str] = []
    _record_steps(type(adapter), monkeypatch, calls)

    DeploymentEngine(adapter).deploy()

    ordered = [call for call in calls if call in {_PHASE_STEP[phase] for phase in _ADR_0002_PHASES}]
    assert ordered == [_PHASE_STEP[phase] for phase in _ADR_0002_PHASES], (
        f"`olf deploy --provider {provider.value}` ran {calls!r}. ADR 0002 fixes the order as "
        f"foundation -> platform -> artifacts: an artifact step that runs before the platform is applied has no "
        f"platform to deploy onto, and a platform apply that waits on one has made a code commit invoke Terraform."
    )
