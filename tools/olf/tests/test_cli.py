from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import olf
from olf.cli import app

runner = CliRunner()


def test_version_command_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == olf.__version__


def test_layers_enabled_exits_nonzero_for_a_disabled_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLAKEFORGE_ANALYTICS_ENABLED", "false")

    result = runner.invoke(app, ["layers", "enabled", "--layer", "analytics"])

    assert result.exit_code == 1


def test_artifacts_deploy_optional_layers_skips_disabled_layers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLAKEFORGE_ANALYTICS_ENABLED", "false")
    monkeypatch.setenv("OPENLAKEFORGE_GOVERNANCE_ENABLED", "false")
    monkeypatch.setattr(
        "olf.commands.artifacts.deploy_superset_reports", lambda: pytest.fail("reports should be skipped")
    )
    monkeypatch.setattr(
        "olf.commands.artifacts.deploy_openmetadata_metadata", lambda: pytest.fail("metadata should be skipped")
    )

    result = runner.invoke(app, ["artifacts", "deploy-optional-layers"])

    assert result.exit_code == 0
    assert "Skipping Superset report assets" in result.output
    assert "Skipping OpenMetadata governance metadata" in result.output


def test_superset_deploy_reports_hydrates_selected_provider_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    options: dict[str, str] = {}
    calls: list[str] = []
    monkeypatch.setattr(
        "olf.commands.runtime.provider_contract_environment",
        lambda **kwargs: options.update(kwargs) or nullcontext(),
    )
    monkeypatch.setattr("olf.commands.superset.deploy_superset_reports", lambda: calls.append("reports"))

    result = runner.invoke(app, ["superset", "deploy-reports", "--provider", "aws"])

    assert result.exit_code == 0
    assert options["provider"] == "aws"
    assert calls == ["reports"]


def test_superset_deploy_reports_threads_a_custom_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    options: dict[str, str] = {}
    monkeypatch.setattr(
        "olf.commands.runtime.provider_contract_environment",
        lambda **kwargs: options.update(kwargs) or nullcontext(),
    )
    monkeypatch.setattr("olf.commands.superset.deploy_superset_reports", lambda: None)

    result = runner.invoke(app, ["superset", "deploy-reports", "--project-root", "/srv/my-project"])

    assert result.exit_code == 0
    assert options["project_root"] == "/srv/my-project"


def test_openmetadata_deploy_hydrates_selected_provider_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    options: dict[str, str] = {}
    calls: list[str] = []
    monkeypatch.setattr(
        "olf.commands.runtime.provider_contract_environment",
        lambda **kwargs: options.update(kwargs) or nullcontext(),
    )
    monkeypatch.setattr("olf.commands.openmetadata.deploy_openmetadata_metadata", lambda: calls.append("metadata"))

    result = runner.invoke(app, ["openmetadata", "deploy-metadata", "--provider", "azure"])

    assert result.exit_code == 0
    assert options["provider"] == "azure"
    assert calls == ["metadata"]


def test_artifacts_upload_hydrates_selected_provider_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    options: dict[str, str] = {}
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(
        "olf.commands.runtime.provider_contract_environment",
        lambda **kwargs: options.update(kwargs) or nullcontext(),
    )
    monkeypatch.setattr("olf.commands.artifacts.upload_manifests", lambda **kwargs: calls.append(kwargs))

    result = runner.invoke(
        app,
        [
            "artifacts",
            "upload-manifests",
            "--provider",
            "aws",
            "--namespace",
            "custom-lakehouse",
            "--via",
            "direct",
        ],
    )

    assert result.exit_code == 0
    assert options["provider"] == "aws"
    assert options["namespace"] == "custom-lakehouse"
    assert calls == [{"via": "direct", "manifest_root": "", "runtime_root": ""}]


def test_dbt_parse_hydrates_selected_provider_contracts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project = tmp_path / "gold/demo/dbt"
    project.mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: demo\nprofile: demo\n")
    options: dict[str, str] = {}
    commands: list[list[str]] = []

    class Runner:
        def run(self, argv, **kwargs):  # noqa: ANN001, ANN003
            commands.append(argv)

    class Resolver:
        def resolve(self, name: str) -> Path:
            assert name == "dbt"
            return Path("dbt")

    tools = SimpleNamespace(runner=Runner(), resolver=Resolver())
    monkeypatch.setattr(
        "olf.commands.runtime.provider_contract_environment",
        lambda **kwargs: options.update(kwargs) or nullcontext(),
    )
    monkeypatch.setattr("olf.commands.dbt.Toolkit.default", lambda: tools)

    result = runner.invoke(
        app,
        [
            "dbt",
            "parse",
            "--project-dir",
            str(project),
            "--provider",
            "aws",
            "--profile",
            "slim",
            "--namespace",
            "custom-lakehouse",
        ],
    )

    assert result.exit_code == 0
    assert options == {
        "provider": "aws",
        "profile": "slim",
        "namespace": "custom-lakehouse",
        "cluster_name": "",
        "kubeconfig_path": "",
        "project_root": "",
    }
    assert commands[-1][-2:] == ["--target", "aws_runtime"]


def test_dbt_parse_discovers_projects_under_the_contract_environments_repo_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Project discovery must read config.repo_root() *after* entering the
    contract environment, since that's what applies --project-root's
    OPENLAKEFORGE_REPO_ROOT - reading it beforehand always resolved the
    ambient/default root instead of a custom --project-root selection.
    """
    project_root = tmp_path / "custom-project"
    project = project_root / "lakehouse_code/gold/demo/dbt"
    project.mkdir(parents=True)
    (project / "dbt_project.yml").write_text("name: demo\nprofile: demo\n")
    commands: list[list[str]] = []

    class Runner:
        def run(self, argv, **kwargs):  # noqa: ANN001, ANN003
            commands.append(argv)

    class Resolver:
        def resolve(self, name: str) -> Path:
            assert name == "dbt"
            return Path("dbt")

    tools = SimpleNamespace(runner=Runner(), resolver=Resolver())

    @contextmanager
    def fake_provider_contract_environment(**kwargs):  # noqa: ANN003, ANN202
        monkeypatch.setenv("OPENLAKEFORGE_REPO_ROOT", kwargs["project_root"])
        yield

    monkeypatch.setattr("olf.commands.runtime.provider_contract_environment", fake_provider_contract_environment)
    monkeypatch.setattr("olf.commands.dbt.Toolkit.default", lambda: tools)

    result = runner.invoke(app, ["dbt", "parse", "--project-root", str(project_root)])

    assert result.exit_code == 0
    assert commands[-1][-2:] == ["--target", "local_runtime"]
    assert str(project) in commands[-1]


def test_dbt_parse_selects_outputs_declared_by_provider_profiles() -> None:
    from olf.commands import dbt

    assert dbt._target_for_provider("local") == "local_runtime"
    assert dbt._target_for_provider("aws") == "aws_runtime"
    assert dbt._target_for_provider("azure") == "local_runtime"


def test_floe_generation_passes_the_selected_namespace_to_the_local_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    options: dict[str, str] = {}
    generated: list[dict] = []

    class FakeLocalProvider:
        config = SimpleNamespace(floe=object())
        tools = object()
        env = {}

    context = SimpleNamespace(
        paths=SimpleNamespace(
            repo_root=tmp_path, distribution_root=tmp_path, platform_terraform_dir=tmp_path / "contracts"
        ),
        namespace="custom-lakehouse",
        features=SimpleNamespace(governance_enabled=True),
    )
    monkeypatch.setattr(
        "olf.commands.deployment._build_context",
        lambda *args, **kwargs: options.update(kwargs) or context,
    )
    monkeypatch.setattr(
        "olf.commands.deployment._build_engine",
        lambda *args, **kwargs: SimpleNamespace(provider=FakeLocalProvider()),
    )
    monkeypatch.setattr("olf.deployment.local.provider.LocalProvider", FakeLocalProvider)
    monkeypatch.setattr(
        "olf.deployment.local.artifacts.applied_contract_environment", lambda *args, **kwargs: nullcontext({})
    )
    monkeypatch.setattr(
        "olf.deployment.floe_manifests.generate_local_manifests", lambda *args, **kwargs: generated.append(kwargs)
    )

    result = runner.invoke(app, ["floe", "generate-manifests", "--namespace", "custom-lakehouse"])

    assert result.exit_code == 0
    assert options["namespace"] == "custom-lakehouse"
    assert generated == [
        {
            "repo_root": tmp_path,
            "distribution_root": tmp_path,
            "namespace": "custom-lakehouse",
            "governance_enabled": True,
            "environ": {},
            "env": {},
        }
    ]


def test_floe_generation_honors_custom_contract_root_for_cloud(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class FakeBackend:
        def generate_floe_manifests(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
            captured["generation"] = kwargs

    class FakeCloudProvider:
        _foundation_facts = SimpleNamespace(kube_context="custom-cluster")
        config = object()
        backend = FakeBackend()
        tools = object()
        env = {}

    contract_root = tmp_path / "custom-contracts"
    context = SimpleNamespace(
        paths=SimpleNamespace(
            repo_root=tmp_path,
            distribution_root=tmp_path,
            platform_terraform_dir=tmp_path / "default-contracts",
            kubeconfig_path=tmp_path / "kubeconfig.yaml",
            port_forward_log_prefix=tmp_path / "port-forward",
        ),
        namespace="custom-lakehouse",
        features=SimpleNamespace(governance_enabled=True),
    )
    monkeypatch.setenv("OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR", str(contract_root))
    monkeypatch.setattr("olf.commands.deployment._build_context", lambda *args, **kwargs: context)
    monkeypatch.setattr(
        "olf.commands.deployment._build_engine",
        lambda *args, **kwargs: SimpleNamespace(provider=FakeCloudProvider()),
    )
    monkeypatch.setattr(
        "olf.deployment.contract_env.applied_contract_environment",
        lambda **kwargs: captured.update(kwargs) or nullcontext({}),
    )

    result = runner.invoke(app, ["floe", "generate-manifests", "--provider", "aws"])

    assert result.exit_code == 0
    assert captured["contract_terraform_dir"] == contract_root
    assert captured["generation"] == {
        "repo_root": tmp_path,
        "distribution_root": tmp_path,
        "namespace": "custom-lakehouse",
        "governance_enabled": True,
        "environ": {},
        "env": {},
    }


def test_revision_compute_command_prints_runtime_artifact_revision(tmp_path: Path) -> None:
    path = tmp_path / "manifests/sales/sales.manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")

    result = runner.invoke(app, ["revision", "compute", "--runtime-root", str(tmp_path)])

    assert result.exit_code == 0
    assert result.output.startswith("sha256:")


def test_superset_export_reports_defaults_come_from_the_first_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    from openlakeforge_domain import inventory_for

    from olf import config

    inventory = inventory_for(config.repo_root())
    default_dashboard = inventory.dashboards[0]
    default_product = next(product for product in inventory.products if product.id == default_dashboard.products[0])
    calls: list[dict] = []
    monkeypatch.setattr(
        "olf.superset.export_report",
        lambda *args, **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr("olf.commands.runtime.provider_contract_environment", lambda **kwargs: nullcontext())

    result = runner.invoke(app, ["superset", "export-reports"])

    assert result.exit_code == 0
    assert calls[0]["report_source_dir"] == default_dashboard.report_source_dir
    assert calls[0]["bundle_name"] == default_dashboard.superset_export_bundle_name
    assert calls[0]["dashboard_title"] == default_product.display_name


def test_superset_export_reports_dashboard_title_prefers_the_bundles_own_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bundle's own dashboard_title must win over displayName when they differ."""
    lakehouse_dir = tmp_path / "lakehouse_code"
    source_dir = lakehouse_dir / "bronze" / "crm"
    source_dir.mkdir(parents=True)
    (source_dir / "source.yaml").write_text(
        """apiVersion: openlakeforge.io/v1alpha3
kind: Source
name: crm
displayName: CRM
description: CRM source.
status: planned
resources:
  - name: orders
""",
        encoding="utf-8",
    )
    (lakehouse_dir / "lakehouse.yaml").write_text(
        """apiVersion: openlakeforge.io/v1alpha3
kind: Lakehouse
name: test
displayName: Test
description: Test lakehouse.
status: planned
sources:
  - crm
domains:
  - name: sales
    displayName: Sales
    description: Sales domain.
    status: planned
    silver_tables:
      tables:
        - name: orders
          source: crm
          resource: orders
    products:
      - id: orders
        displayName: Sales Orders Product Metadata Name
        description: Sales orders.
        status: planned
        silver_inputs: [orders]
        gold_tables:
          tables:
            - name: mart_orders
dashboards:
  - name: orders
    products: [orders]
""",
        encoding="utf-8",
    )
    dashboards_dir = lakehouse_dir / "dashboards" / "superset" / "orders" / "dashboards"
    dashboards_dir.mkdir(parents=True)
    (dashboards_dir / "Live_1.yaml").write_text(
        "dashboard_title: The Actual Live Dashboard Title\nslug: sales-orders-live\n", encoding="utf-8"
    )

    monkeypatch.setenv("OPENLAKEFORGE_REPO_ROOT", str(tmp_path))
    calls: list[dict] = []
    monkeypatch.setattr("olf.superset.export_report", lambda *args, **kwargs: calls.append(kwargs))
    monkeypatch.setattr("olf.commands.runtime.provider_contract_environment", lambda **kwargs: nullcontext())

    result = runner.invoke(app, ["superset", "export-reports"])

    assert result.exit_code == 0
    assert calls[0]["dashboard_title"] == "The Actual Live Dashboard Title"


def test_catalog_sync_namespaces_dispatches_to_glue_for_aws_glue_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from olf.commands import catalog

    calls: list[dict] = []
    monkeypatch.setenv("OPENLAKEFORGE_CATALOG_PROVIDER", "aws-glue")
    monkeypatch.setattr(
        catalog,
        "_sync_glue_namespaces",
        lambda *, desired, dry_run, prune: calls.append(
            {"backend": "glue", "dry_run": dry_run, "prune": prune}
        ),
    )
    monkeypatch.setattr(
        catalog, "_sync_polaris_namespaces", lambda **kwargs: calls.append({"backend": "polaris"})
    )

    result = runner.invoke(app, ["catalog", "sync-namespaces", "--dry-run"])

    assert result.exit_code == 0
    assert calls == [{"backend": "glue", "dry_run": True, "prune": False}]


def test_catalog_sync_namespaces_dispatches_to_polaris_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from olf.commands import catalog

    calls: list[dict] = []
    monkeypatch.delenv("OPENLAKEFORGE_CATALOG_PROVIDER", raising=False)
    monkeypatch.setattr(
        catalog,
        "_sync_polaris_namespaces",
        lambda *, desired, dry_run, prune: calls.append(
            {"backend": "polaris", "dry_run": dry_run, "prune": prune}
        ),
    )
    monkeypatch.setattr(
        catalog, "_sync_glue_namespaces", lambda **kwargs: calls.append({"backend": "glue"})
    )

    result = runner.invoke(app, ["catalog", "sync-namespaces", "--prune"])

    assert result.exit_code == 0
    assert calls == [{"backend": "polaris", "dry_run": False, "prune": True}]


def test_catalog_sync_namespaces_treats_false_prune_environment_as_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from olf.commands import catalog

    calls: list[dict] = []
    monkeypatch.setenv("OPENLAKEFORGE_CATALOG_PRUNE_NAMESPACES", "false")
    monkeypatch.setattr(
        catalog,
        "_sync_polaris_namespaces",
        lambda *, desired, dry_run, prune: calls.append({"dry_run": dry_run, "prune": prune}),
    )

    result = runner.invoke(app, ["catalog", "sync-namespaces"])

    assert result.exit_code == 0
    assert calls == [{"dry_run": False, "prune": False}]


def test_catalog_sync_namespaces_rejects_an_unknown_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from olf.commands import catalog

    calls: list[str] = []
    monkeypatch.setenv("OPENLAKEFORGE_CATALOG_PROVIDER", "snowflake")
    monkeypatch.setattr(catalog, "_sync_polaris_namespaces", lambda **kwargs: calls.append("polaris"))
    monkeypatch.setattr(catalog, "_sync_glue_namespaces", lambda **kwargs: calls.append("glue"))

    result = runner.invoke(app, ["catalog", "sync-namespaces"])

    assert result.exit_code == 1
    assert calls == []
    assert "snowflake" in result.output
