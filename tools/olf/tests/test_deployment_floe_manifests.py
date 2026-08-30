from __future__ import annotations

from pathlib import Path

import pytest
from _tooling_support import RecordingRunner

from olf.deployment import floe_manifests
from olf.deployment.engine import Toolkit
from olf.deployment.errors import DeploymentPreconditionError
from olf.deployment.floe_manifests import FloeManifestSettings
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver


def _toolkit(runner: RecordingRunner) -> Toolkit:
    from olf.tooling.aws import AwsCli
    from olf.tooling.azure import AzureCli
    from olf.tooling.docker import Docker
    from olf.tooling.helm import Helm
    from olf.tooling.kind import Kind
    from olf.tooling.kubectl import Kubectl
    from olf.tooling.terraform import Terraform

    resolver = PathExecutableResolver(
        overrides={t: Path(t) for t in ("terraform", "docker", "kind", "kubectl", "helm")}
    )
    return Toolkit(
        runner=runner,
        resolver=resolver,
        terraform=Terraform(runner, resolver),
        helm=Helm(runner, resolver),
        kubectl=Kubectl(runner, resolver),
        docker=Docker(runner, resolver),
        kind=Kind(runner, resolver),
        aws=AwsCli(runner, resolver),
        azure=AzureCli(runner, resolver),
    )


def _make_repo(tmp_path: Path) -> Path:
    repo_root = tmp_path / "repo"
    (repo_root / "lakehouse_code/silver/orders/contracts/floe").mkdir(parents=True)
    (repo_root / "lakehouse_code/silver/orders/contracts/floe/orders.yml").write_text("sources: []\n")
    (repo_root / "libs/floe/profiles").mkdir(parents=True)
    (repo_root / "libs/floe/profiles/local-k8s.yml").write_text("apiVersion: floe/v1\n")
    return repo_root


def _settings(repo_root: Path) -> FloeManifestSettings:
    return FloeManifestSettings(
        version="0.6.11",
        image="ghcr.io/malon64/floe:0.6.11",
        runtime="image",
        runtime_artifact_dir=repo_root / ".tmp/floe-runtime/local",
        platform=None,
    )


def test_discover_floe_configs_finds_domain_configs(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)

    configs = floe_manifests.discover_floe_configs(repo_root)

    assert configs == [repo_root / "lakehouse_code/silver/orders/contracts/floe/orders.yml"]


def test_generate_local_manifests_uses_checked_in_profile_when_governance_enabled(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    settings = _settings(repo_root)
    runner = RecordingRunner(CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0))
    tools = _toolkit(runner)

    manifests = floe_manifests.generate_local_manifests(
        settings,
        tools,
        repo_root=repo_root,
        distribution_root=repo_root,
        namespace="olf-dev",
        governance_enabled=True,
        environ={},
        env={},
    )

    assert manifests == [settings.runtime_artifact_dir / "manifests/orders/orders.manifest.json"]
    profile_copy = settings.runtime_artifact_dir / "profiles/orders/local-k8s.yml"
    assert profile_copy.read_text() == "apiVersion: floe/v1\n"
    config_copy = settings.runtime_artifact_dir / "configs/orders/orders.yml"
    assert config_copy.read_text() == "sources: []\n"


def test_generate_local_manifests_renders_profile_when_governance_disabled(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    settings = _settings(repo_root)
    runner = RecordingRunner(CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0))
    tools = _toolkit(runner)

    floe_manifests.generate_local_manifests(
        settings,
        tools,
        repo_root=repo_root,
        distribution_root=repo_root,
        namespace="olf-dev",
        governance_enabled=False,
        environ={"OPENLAKEFORGE_GOVERNANCE_ENABLED": "false"},
        env={},
    )

    rendered_profile = settings.runtime_artifact_dir / "profiles/local-k8s.yml"
    assert rendered_profile.is_file()
    assert "EnvironmentProfile" in rendered_profile.read_text()


def test_generate_local_manifests_runs_validate_then_generate_via_docker(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    settings = _settings(repo_root)
    runner = RecordingRunner(CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0))
    tools = _toolkit(runner)

    floe_manifests.generate_local_manifests(
        settings,
        tools,
        repo_root=repo_root,
        distribution_root=repo_root,
        namespace="olf-dev",
        governance_enabled=True,
        environ={},
        env={},
    )

    subcommands = [call.argv[call.argv.index(settings.image) + 1] for call in runner.calls]
    assert subcommands == ["validate", "manifest"]
    validate_call = runner.calls[0]
    assert "-v" in validate_call.argv
    assert f"{repo_root}:/work" in validate_call.argv
    assert "-c" in validate_call.argv
    generate_call = runner.calls[1]
    assert "--deterministic" in generate_call.argv
    assert "--manifest-name" in generate_call.argv
    assert "orders.local" in generate_call.argv
    assert generate_call.argv[generate_call.argv.index("--default-domain") + 1] == "orders"


def test_generate_local_manifests_makes_manifest_dir_writable_by_the_container_user(tmp_path: Path) -> None:
    import stat

    repo_root = _make_repo(tmp_path)
    settings = _settings(repo_root)
    tools = _toolkit(RecordingRunner(CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0)))

    floe_manifests.generate_local_manifests(
        settings,
        tools,
        repo_root=repo_root,
        distribution_root=repo_root,
        namespace="olf-dev",
        governance_enabled=True,
        environ={},
        env={},
    )

    manifest_dir = settings.runtime_artifact_dir / "manifests/orders"
    mode = stat.S_IMODE(manifest_dir.stat().st_mode)
    assert mode == 0o777


def test_generate_local_manifests_mounts_runtime_root_outside_repo_root(tmp_path: Path) -> None:
    """An installed distribution's `runtime_artifact_dir` lives under
    `OLF_HOME/work`, outside the read-only payload `repo_root` resolves to -
    generated config/manifest paths must route through a `/runtime` mount
    rather than an absolute host path the Floe container never sees."""
    repo_root = _make_repo(tmp_path)
    runtime_root = tmp_path / "olf-home" / "work" / "floe-runtime" / "local"
    settings = FloeManifestSettings(
        version="0.6.11",
        image="ghcr.io/malon64/floe:0.6.11",
        runtime="image",
        runtime_artifact_dir=runtime_root,
        platform=None,
    )
    runner = RecordingRunner(CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0))
    tools = _toolkit(runner)

    manifests = floe_manifests.generate_local_manifests(
        settings,
        tools,
        repo_root=repo_root,
        distribution_root=repo_root,
        namespace="olf-dev",
        governance_enabled=True,
        environ={},
        env={},
    )

    assert manifests == [runtime_root / "manifests/orders/orders.manifest.json"]
    validate_call = runner.calls[0]
    assert f"{repo_root}:/work" in validate_call.argv
    assert f"{runtime_root}:/runtime" in validate_call.argv
    config_flag_index = validate_call.argv.index("-c") + 1
    assert validate_call.argv[config_flag_index] == "/runtime/configs/orders/orders.yml"
    # RenderedProfileStrategy always copies the (checked-in or rendered)
    # profile into runtime_root/profiles/<domain>/, so even the governance
    # case resolves through /runtime, not /work.
    profile_flag_index = validate_call.argv.index("-p") + 1
    assert validate_call.argv[profile_flag_index] == "/runtime/profiles/orders/local-k8s.yml"

    generate_call = runner.calls[1]
    output_flag_index = generate_call.argv.index("--output") + 1
    assert generate_call.argv[output_flag_index] == "/runtime/manifests/orders/orders.manifest.json"


def test_generate_local_manifests_mounts_rendered_profile_under_runtime_root(tmp_path: Path) -> None:
    """A rendered (non-governance, non-default-namespace) profile also lives
    under `runtime_artifact_dir` and must resolve through `/runtime`, not
    `/work`, once the runtime root is outside `repo_root`."""
    repo_root = _make_repo(tmp_path)
    runtime_root = tmp_path / "olf-home" / "work" / "floe-runtime" / "local"
    settings = FloeManifestSettings(
        version="0.6.11",
        image="ghcr.io/malon64/floe:0.6.11",
        runtime="image",
        runtime_artifact_dir=runtime_root,
        platform=None,
    )
    runner = RecordingRunner(CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0))
    tools = _toolkit(runner)

    floe_manifests.generate_local_manifests(
        settings,
        tools,
        repo_root=repo_root,
        distribution_root=repo_root,
        namespace="olf-dev",
        governance_enabled=False,
        environ={"OPENLAKEFORGE_GOVERNANCE_ENABLED": "false"},
        env={},
    )

    validate_call = runner.calls[0]
    profile_flag_index = validate_call.argv.index("-p") + 1
    assert validate_call.argv[profile_flag_index] == "/runtime/profiles/orders/local-k8s.yml"


def test_mounted_container_path_prefers_runtime_root_then_falls_back_to_work(tmp_path: Path) -> None:
    from olf.deployment.floe_manifests import _mounted_container_path

    repo_root = tmp_path / "repo"
    runtime_root = tmp_path / "olf-home" / "work" / "floe-runtime" / "local"

    def _mount(path: Path) -> str:
        return _mounted_container_path(path, repo_root=repo_root, runtime_root=runtime_root)

    assert _mount(runtime_root) == "/runtime"
    assert _mount(runtime_root / "manifests/orders/orders.json") == "/runtime/manifests/orders/orders.json"
    assert _mount(repo_root / "libs/floe/profiles/local-k8s.yml") == "/work/libs/floe/profiles/local-k8s.yml"
    assert _mount(repo_root) == "/work"


def test_generate_local_manifests_raises_when_no_configs_found(tmp_path: Path) -> None:
    repo_root = tmp_path / "empty-repo"
    (repo_root / "lakehouse_code/silver").mkdir(parents=True)
    (repo_root / "libs/floe/profiles").mkdir(parents=True)
    (repo_root / "libs/floe/profiles/local-k8s.yml").write_text("apiVersion: floe/v1\n")
    settings = _settings(repo_root)
    tools = _toolkit(RecordingRunner())

    with pytest.raises(DeploymentPreconditionError):
        floe_manifests.generate_local_manifests(
            settings,
        tools,
        repo_root=repo_root,
        distribution_root=repo_root,
        namespace="olf-dev",
        governance_enabled=True,
        environ={},
        env={},
        )


def test_generate_local_manifests_rejects_multiple_configs_for_one_domain(tmp_path: Path) -> None:
    repo_root = _make_repo(tmp_path)
    (repo_root / "lakehouse_code/silver/orders/contracts/floe/extra.yml").write_text("sources: []\n")
    settings = _settings(repo_root)
    tools = _toolkit(RecordingRunner())

    with pytest.raises(DeploymentPreconditionError, match="duplicate configs found for: orders"):
        floe_manifests.generate_local_manifests(
            settings,
        tools,
        repo_root=repo_root,
        distribution_root=repo_root,
        namespace="olf-dev",
        governance_enabled=True,
        environ={},
        env={},
        )


# --- AWS Glue profile strategy -----------------------------------------------


def test_aws_glue_profile_strategy_renders_profile_with_domain_glue_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen_environ: dict = {}

    def _fake_render_profile(environ):  # noqa: ANN001, ANN202
        seen_environ.update(environ)
        return "apiVersion: floe/v1\nkind: EnvironmentProfile\n"

    monkeypatch.setattr(floe_manifests.floe_module, "render_profile", _fake_render_profile)

    strategy = floe_manifests.AwsGlueProfileStrategy(
        environ={"OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON": '{"orders": "orders_silver"}'}
    )
    profile_dir = tmp_path / "profiles/orders"

    profile_path = strategy.profile_for(domain="orders", profile_dir=profile_dir)

    assert profile_path == profile_dir / "aws-eks.yml"
    assert profile_path.is_file()
    assert seen_environ["OPENLAKEFORGE_CATALOG_GLUE_DATABASE"] == "orders_silver"


def test_aws_glue_profile_strategy_raises_when_domain_namespace_missing(tmp_path: Path) -> None:
    strategy = floe_manifests.AwsGlueProfileStrategy(
        environ={"OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON": '{"other_domain": "other_silver"}'}
    )

    with pytest.raises(DeploymentPreconditionError, match="missing Silver catalog namespace"):
        strategy.profile_for(domain="orders", profile_dir=tmp_path / "profiles/orders")


def test_aws_glue_profile_strategy_raises_on_invalid_namespaces_json(tmp_path: Path) -> None:
    strategy = floe_manifests.AwsGlueProfileStrategy(
        environ={"OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON": "not-json"}
    )

    with pytest.raises(DeploymentPreconditionError, match="invalid OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON"):
        strategy.profile_for(domain="orders", profile_dir=tmp_path / "profiles/orders")


def test_generate_aws_manifests_uses_a_distinct_profile_per_domain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "lakehouse_code/silver/orders/contracts/floe").mkdir(parents=True)
    (repo_root / "lakehouse_code/silver/orders/contracts/floe/orders.yml").write_text("sources: []\n")
    (repo_root / "lakehouse_code/silver/inventory/contracts/floe").mkdir(parents=True)
    (repo_root / "lakehouse_code/silver/inventory/contracts/floe/inventory.yml").write_text("sources: []\n")

    def _fake_render_profile(environ):  # noqa: ANN001, ANN202
        return f"database: {environ['OPENLAKEFORGE_CATALOG_GLUE_DATABASE']}\n"

    monkeypatch.setattr(floe_manifests.floe_module, "render_profile", _fake_render_profile)

    settings = floe_manifests.FloeManifestSettings(
        version="0.6.11",
        image="ghcr.io/malon64/floe:0.6.11",
        runtime="image",
        runtime_artifact_dir=repo_root / ".tmp/floe-runtime/aws",
        platform=None,
    )
    runner = RecordingRunner(CommandResult(argv=(), returncode=0, stdout="", stderr="", duration_seconds=0.0))
    tools = _toolkit(runner)
    environ = {
        "OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON": (
            '{"orders": "orders_silver", "inventory": "inventory_silver"}'
        )
    }

    floe_manifests.generate_aws_manifests(settings, tools, repo_root=repo_root, environ=environ, env={})

    orders_profile = settings.runtime_artifact_dir / "profiles/orders/aws-eks.yml"
    inventory_profile = settings.runtime_artifact_dir / "profiles/inventory/aws-eks.yml"
    assert orders_profile.read_text() == "database: orders_silver\n"
    assert inventory_profile.read_text() == "database: inventory_silver\n"
