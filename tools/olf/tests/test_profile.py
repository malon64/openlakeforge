from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from olf.deployment.context import Provider
from olf.profile import (
    DeploymentProfileError,
    Preset,
    StageName,
    legacy_single_stage_topology,
    load_deployment_profile,
    resolve_topology,
    validate_deployment_profile,
)

FIXTURES = Path(__file__).parent / "fixtures" / "profiles"


def _load_fixture(name: str) -> dict:
    return yaml.safe_load((FIXTURES / name).read_text())


def test_load_deployment_profile_parses_the_repo_root_profile() -> None:
    repo_root = Path(__file__).resolve().parents[3]

    profile = load_deployment_profile(repo_root / "openlakeforge.yaml")

    assert profile.name == "openlakeforge"
    assert profile.provider.type == Provider.LOCAL
    assert profile.preset == Preset.SLIM
    assert len(profile.stages) == 1
    assert profile.stage(StageName.DEV) is not None
    assert profile.stage(StageName.DEV).enabled is True


def test_valid_slim_local_profile_resolves_dev_only_with_no_capabilities() -> None:
    profile = validate_deployment_profile(_load_fixture("valid_slim_local.yaml"))
    topology = resolve_topology(profile)

    assert topology.provider == Provider.LOCAL
    assert topology.region is None
    assert topology.preset == Preset.SLIM

    dev = topology.stage(StageName.DEV)
    assert dev.enabled is True
    assert dev.capabilities.analytics is False
    assert dev.capabilities.governance is False

    for stage_name in (StageName.UAT, StageName.PROD):
        stage = topology.stage(stage_name)
        assert stage.enabled is False
        assert stage.capabilities.analytics is False
        assert stage.capabilities.governance is False


def test_valid_full_aws_profile_applies_preset_defaults_and_explicit_overrides() -> None:
    profile = validate_deployment_profile(_load_fixture("valid_full_aws_with_uat_prod.yaml"))
    topology = resolve_topology(profile)

    assert topology.provider == Provider.AWS
    assert topology.region == "eu-west-3"
    assert topology.preset == Preset.FULL

    # dev/uat wrote no capabilities -> the 'full' preset default applies.
    for stage_name in (StageName.DEV, StageName.UAT):
        stage = topology.stage(stage_name)
        assert stage.enabled is True
        assert stage.capabilities.analytics is True
        assert stage.capabilities.governance is True

    # prod explicitly overrides governance -> explicit wins over the preset.
    prod = topology.stage(StageName.PROD)
    assert prod.enabled is True
    assert prod.capabilities.analytics is True
    assert prod.capabilities.governance is False


def test_disabled_stage_forces_capabilities_false_even_if_written_true() -> None:
    document = _load_fixture("valid_full_aws_with_uat_prod.yaml")
    document["spec"]["stages"]["uat"] = {"enabled": False, "capabilities": {"analytics": True, "governance": True}}
    profile = validate_deployment_profile(document)

    topology = resolve_topology(profile)

    uat = topology.stage(StageName.UAT)
    assert uat.enabled is False
    assert uat.capabilities.analytics is False
    assert uat.capabilities.governance is False


def test_missing_stage_resolves_disabled_and_prod_is_never_implicit() -> None:
    profile = validate_deployment_profile(_load_fixture("valid_slim_local.yaml"))

    topology = resolve_topology(profile)

    assert topology.stage(StageName.PROD).enabled is False


def test_render_json_is_stable_across_repeated_resolutions() -> None:
    profile = validate_deployment_profile(_load_fixture("valid_full_aws_with_uat_prod.yaml"))

    first = resolve_topology(profile).render_json()
    second = resolve_topology(profile).render_json()

    assert first == second


@pytest.mark.parametrize(
    ("fixture_name", "match"),
    [
        ("invalid_unsupported_api_version.yaml", "unsupported apiVersion"),
        ("invalid_unknown_envelope_field.yaml", "must not contain"),
        ("invalid_unknown_stage.yaml", "unknown stage"),
        ("invalid_no_stage_enabled.yaml", "at least one stage must be enabled"),
        ("invalid_prod_without_dev.yaml", "cannot be enabled while 'dev' is disabled"),
        ("invalid_local_with_region.yaml", "region must not be set"),
        ("invalid_unknown_provider_type.yaml", "spec.provider.type must be one of"),
        ("invalid_unknown_capability_field.yaml", "must not contain"),
    ],
)
def test_validate_deployment_profile_rejects_every_illegal_shape(fixture_name: str, match: str) -> None:
    document = _load_fixture(fixture_name)

    with pytest.raises(DeploymentProfileError, match=match):
        validate_deployment_profile(document)


def test_validate_deployment_profile_rejects_wrong_kind() -> None:
    document = _load_fixture("valid_slim_local.yaml")
    document["kind"] = "NotAProfile"

    with pytest.raises(DeploymentProfileError, match="kind must be"):
        validate_deployment_profile(document)


@pytest.mark.parametrize("field", ["bucket", "catalog", "endpoint", "credentials"])
def test_validate_deployment_profile_rejects_provider_physical_details(field: str) -> None:
    document = _load_fixture("valid_slim_local.yaml")
    document["spec"]["provider"][field] = "provider-owned"

    with pytest.raises(DeploymentProfileError, match="must not contain"):
        validate_deployment_profile(document)


def test_validate_deployment_profile_rejects_non_mapping_document() -> None:
    with pytest.raises(DeploymentProfileError, match="must contain a YAML object"):
        validate_deployment_profile([])  # type: ignore[arg-type]


def test_load_deployment_profile_reports_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        load_deployment_profile("/nonexistent/openlakeforge.yaml")


def test_legacy_single_stage_topology_matches_the_v02_slim_shorthand() -> None:
    topology = legacy_single_stage_topology(provider=Provider.LOCAL, preset=Preset.SLIM)

    assert topology.provider == Provider.LOCAL
    dev = topology.stage(StageName.DEV)
    assert dev.enabled is True
    assert dev.capabilities.analytics is False
    assert dev.capabilities.governance is False
    for stage_name in (StageName.UAT, StageName.PROD):
        assert topology.stage(stage_name).enabled is False


def test_legacy_single_stage_topology_matches_the_v02_full_shorthand() -> None:
    topology = legacy_single_stage_topology(provider=Provider.LOCAL, preset=Preset.FULL)

    dev = topology.stage(StageName.DEV)
    assert dev.capabilities.analytics is True
    assert dev.capabilities.governance is True
