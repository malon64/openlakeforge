from __future__ import annotations

import json

import pytest
from conftest import CONFORMANCE_PROFILE, CONFORMANCE_STAGES, E2E_REPO_ROOT

from olf.commands._shared import deployment_context_for_profile
from olf.contracts_check import profile_schema_errors
from olf.deployment.context import DeploymentContext, Provider, stage_namespace, topology_variables
from olf.profile import Preset, StageName, load_deployment_profile, resolve_topology


def test_conformance_profile_resolves_a_non_vacuous_local_baseline() -> None:
    assert profile_schema_errors(E2E_REPO_ROOT, profile_path=CONFORMANCE_PROFILE) == []

    topology = resolve_topology(load_deployment_profile(CONFORMANCE_PROFILE))
    enabled = tuple(stage.name for stage in topology.stages if stage.enabled)

    assert topology.provider == Provider.LOCAL
    assert topology.preset == Preset.SLIM
    assert enabled == CONFORMANCE_STAGES
    assert all(
        not stage.capabilities.analytics and not stage.capabilities.governance
        for stage in topology.stages
        if stage.enabled
    )


@pytest.mark.parametrize("stage", CONFORMANCE_STAGES, ids=[stage.value for stage in CONFORMANCE_STAGES])
def test_selecting_a_stage_preserves_the_two_stage_topology(stage: StageName) -> None:
    context: DeploymentContext = deployment_context_for_profile(str(CONFORMANCE_PROFILE), stage=stage.value)
    other = next(item for item in CONFORMANCE_STAGES if item != stage)
    stages = json.loads(topology_variables(context)["stages"])

    assert context.stage == stage
    assert context.namespace == stage_namespace(stage) != stage_namespace(other)
    assert tuple(name for name, value in sorted(stages.items()) if value["enabled"]) == tuple(
        item.value for item in CONFORMANCE_STAGES
    )
