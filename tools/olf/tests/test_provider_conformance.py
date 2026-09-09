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
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import hcl2
import pytest
from _tooling_support import RecordedCall, RecordingRunner
from conftest import write_two_product_fixture

from olf.deployment.context import DeploymentContext, Provider, stage_namespace
from olf.deployment.engine import (
    DeploymentEngine,
    DeploymentPhase,
    DeploymentProvider,
    Toolkit,
    build_provider,
)
from olf.deployment.errors import DeploymentPreconditionError
from olf.deployment.local.platform import (
    require_no_shared_namespace_replacement,
    require_no_stage_removal,
)
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

_FAKE_TOOLS = ("terraform", "docker", "kind", "kubectl", "helm")

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

# Names no stage-scoped identity may carry: the providers themselves, and the
# implementation each happens to be built on. AGENTS.md architectural rule 2 -
# a physical name derived from anything but logical identity makes the same
# descriptor resolve differently per provider.
_PROVIDER_TOKENS = tuple(sorted({provider.value for provider in Provider} | {"seaweedfs", "polaris", "glue"}))


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


def _guard_runner(
    *,
    applied_stages: str | None = None,
    legacy_namespace_exists: bool = False,
    labelled_namespaces: str = "",
) -> RecordingRunner:
    """A runner standing in for a cluster the destructive-apply guards query:
    `terraform output -json stage_names`, the pre-v0.3 shared namespace, and
    the namespaces labelled as this deployment's."""

    class _Runner(RecordingRunner):
        def run(self, command, **kwargs):  # type: ignore[override]  # noqa: ANN001, ANN202
            argv = list(command.argv) if hasattr(command, "argv") else [str(part) for part in command]
            self.calls.append(RecordedCall(argv=argv, kwargs=kwargs))
            if "stage_names" in argv:
                return _ok(applied_stages if applied_stages is not None else "[]")
            if "get" in argv and "namespace" in argv and "lakehouse" in argv:
                return _ok() if legacy_namespace_exists else _fail()
            if "namespace" in argv and "-l" in argv:
                return _ok(labelled_namespaces)
            return _ok()

    return _Runner()


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
        implementation = getattr(type(adapter), method, None)
        assert implementation is not None, (
            f"{type(adapter).__name__} does not implement {method}(). Every DeploymentProvider member is a "
            f"lifecycle step `olf deploy`/`olf destroy` or a Make target delegate calls directly."
        )
        expected = inspect.signature(getattr(DeploymentProvider, method))
        actual = inspect.signature(implementation)
        assert str(actual) == str(expected), (
            f"{type(adapter).__name__}.{method}{actual} does not match DeploymentProvider.{method}{expected}. "
            f"The engine calls every adapter through the same signature."
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
def test_stage_scoped_identities_derive_from_stage_identity_alone(provider: Provider) -> None:
    """AGENTS.md architectural rule 2: physical names are derived from logical
    identity, never from the provider serving it. A provider name embedded in
    a namespace, catalog, or bucket makes the same descriptor resolve to a
    different physical object per provider."""
    contract = _contract(provider)
    parsed = parse_provider_contracts(contract, _topology_of(contract))

    for name, stage in parsed.stages.items():
        derived = {
            "namespace": (stage.namespace, stage_namespace(name)),
            "catalog.catalog_name": (stage.catalog["catalog_name"], f"lakehouse_{name.value}"),
            "activation.prefix": (stage.activation["prefix"], f"activations/{name.value}"),
        }
        for field, (actual, canonical) in derived.items():
            assert actual == canonical, (
                f"{provider.value}'s {name.value!r} stage names its {field} {actual!r}, not the canonical "
                f"{canonical!r}. It is derived from the stage's logical identity and nothing else, so it reads "
                f"the same on every provider."
            )
        identities = [stage.namespace, stage.catalog["catalog_name"], stage.activation["prefix"]]
        identities.append(str(stage.runtime_identity["principal"]))
        identities.extend(str(stage.storage[layer]["bucket_name"]) for layer in ("bronze", "silver", "gold"))
        for identity in identities:
            named = [token for token in _PROVIDER_TOKENS if token in identity.lower()]
            assert not named, (
                f"{provider.value}'s {name.value!r} stage carries {named!r} inside the identity {identity!r}. "
                f"Physical names are derived from logical identity; naming the provider or its implementation "
                f"there is what makes a descriptor stop being provider-neutral."
            )
        for layer in ("bronze", "silver", "gold"):
            bucket = str(stage.storage[layer]["bucket_name"])
            assert name.value in bucket and layer in bucket, (
                f"{provider.value}'s {name.value!r} {layer} bucket is named {bucket!r}, which does not carry its "
                f"own stage and layer. Stage data-plane isolation depends on those names never colliding."
            )


def test_stage_identities_are_identical_across_providers() -> None:
    """The same profile, deployed to a different provider, resolves a stage to
    the same logical identities. This is rule 2 stated as an equality rather
    than as a naming convention: it is what makes an adapter swappable."""
    parsed = {}
    for provider in Provider:
        contract = _contract(provider)
        parsed[provider] = parse_provider_contracts(contract, _topology_of(contract))
    profiles = {provider.value: contracts.deployment["profile_name"] for provider, contracts in parsed.items()}
    assert len(set(profiles.values())) == 1, (
        f"the captured contracts describe different Deployment Profiles ({profiles!r}), so nothing here compares "
        f"like with like. Capture every provider's contract from the same profile."
    )

    shared_stages = set.intersection(*(set(contracts.stages) for contracts in parsed.values()))
    assert shared_stages, "no stage is enabled on every provider's captured contract, so nothing is compared."
    for stage in sorted(shared_stages, key=lambda name: name.value):
        identities = {
            provider.value: (
                contracts.for_stage(stage).namespace,
                contracts.for_stage(stage).catalog["catalog_name"],
                contracts.for_stage(stage).activation["prefix"],
                contracts.for_stage(stage).runtime_identity["principal"],
                tuple(
                    contracts.for_stage(stage).storage[layer]["bucket_name"] for layer in ("bronze", "silver", "gold")
                ),
            )
            for provider, contracts in parsed.items()
        }
        assert len(set(identities.values())) == 1, (
            f"the {stage.value!r} stage resolves to different identities per provider: {identities!r}. A physical "
            f"name that changes with the provider is one derived from the provider rather than from the "
            f"descriptor."
        )


@every_provider
def test_removing_an_applied_stage_is_refused(provider: Provider, tmp_path: Path) -> None:
    """The destructive-apply guard, exercised through each provider's own
    config. It is called from both `local/platform.py` and `cloud/platform.py`
    but declared once, against `LocalDeploymentConfig` - so on AWS and Azure it
    works by duck typing, unchecked (#187, `docs/technical-debt.md`). What it
    protects is an apply that is already deleting a namespace."""
    adapter = _adapter(
        provider,
        tmp_path,
        runner=_guard_runner(applied_stages='["dev", "prod"]'),
        topology=_single_stage_topology(provider, enabled=(StageName.DEV,)),
    )

    with pytest.raises(DeploymentPreconditionError, match="prod"):
        require_no_stage_removal(adapter.config, adapter.tools, env={})


@every_provider
def test_a_stage_dropped_only_from_the_cluster_is_refused(provider: Provider, tmp_path: Path) -> None:
    """The drift path: Terraform state is gone, so `stage_names` reports
    nothing applied, and the namespaces still labelled as this deployment's are
    the only signal left that a dropped stage is deployed."""
    adapter = _adapter(
        provider,
        tmp_path,
        runner=_guard_runner(labelled_namespaces="olf-system\nolf-dev\nolf-prod\n"),
        topology=_single_stage_topology(provider, enabled=(StageName.DEV,)),
    )

    with pytest.raises(DeploymentPreconditionError, match="prod"):
        require_no_stage_removal(adapter.config, adapter.tools, env={})


@every_provider
def test_stage_removal_proceeds_under_the_explicit_opt_in(provider: Provider, tmp_path: Path) -> None:
    """`--allow-stage-removal` is the one thing that makes the apply proceed,
    and it means the same on every provider."""
    adapter = _adapter(
        provider,
        tmp_path,
        runner=_guard_runner(applied_stages='["dev", "prod"]'),
        topology=_single_stage_topology(provider, enabled=(StageName.DEV,)),
        allow_stage_removal=True,
    )

    require_no_stage_removal(adapter.config, adapter.tools, env={})


@every_provider
def test_replacing_the_pre_v0_3_shared_namespace_is_refused(provider: Provider, tmp_path: Path) -> None:
    """Every root's shared-services namespace was `lakehouse` before the
    stage-aware rewrite. A namespace name is immutable, so an apply against an
    unmigrated cluster plans to destroy it - and the SeaweedFS, PostgreSQL, and
    Polaris state inside it - to create an empty `olf-system`."""
    adapter = _adapter(
        provider,
        tmp_path,
        runner=_guard_runner(legacy_namespace_exists=True),
        topology=_single_stage_topology(provider, enabled=(StageName.DEV,)),
    )

    with pytest.raises(DeploymentPreconditionError, match="lakehouse"):
        require_no_shared_namespace_replacement(adapter.config, adapter.tools, env={})


@every_provider
def test_a_fresh_cluster_has_no_shared_namespace_to_replace(provider: Provider, tmp_path: Path) -> None:
    adapter = _adapter(
        provider,
        tmp_path,
        runner=_guard_runner(legacy_namespace_exists=False),
        topology=_single_stage_topology(provider, enabled=(StageName.DEV,)),
    )

    require_no_shared_namespace_replacement(adapter.config, adapter.tools, env={})


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
