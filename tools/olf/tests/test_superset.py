import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from olf import k8s, superset
from olf.contracts import build_contract_env
from olf.profile import resolve_topology, validate_deployment_profile

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _stage_environment(fixture_name: str, stage: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """One stage's hydrated contract environment, as the CLI would see it.

    Mirrors `deployment.contract_env.applied_contract_environment`: the
    exports overlay the caller's own environment rather than replacing it,
    which is what lets a caller-set OPENLAKEFORGE_QUERY_SQLALCHEMY_URI
    survive (`olf.contracts` module docstring) and therefore what makes the
    stale-URI case below reachable at all.
    """
    contract = json.loads((FIXTURES / fixture_name).read_text())
    deployment = contract["deployment"]
    topology = resolve_topology(
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
                                "analytics": "reporting" in payload,
                                "governance": "governance" in payload,
                            },
                        }
                        for name, payload in contract["stages"].items()
                    },
                },
            }
        )
    )
    resolved = dict(base or {})
    exports, unsets = build_contract_env(resolved, contract, repo_root=REPO_ROOT, topology=topology, stage=stage)
    resolved.update(exports)
    for name in unsets:
        resolved.pop(name, None)
    return resolved


def test_resolve_stage_report_target_reads_the_named_stages_own_bindings() -> None:
    dev = superset.resolve_stage_report_target(
        _stage_environment("aws-provider-contracts-v3.json", "dev"), stage="dev"
    )
    prod = superset.resolve_stage_report_target(
        _stage_environment("aws-provider-contracts-v3.json", "prod"), stage="prod"
    )

    assert (dev.namespace, prod.namespace) == ("olf-dev", "olf-prod")
    assert dev.sqlalchemy_uri.endswith("/lakehouse_dev")
    assert prod.sqlalchemy_uri.endswith("/lakehouse_prod")
    assert (dev.schema_prefix, prod.schema_prefix) == ("lakehouse_dev_", "lakehouse_prod_")


def test_resolve_stage_report_target_names_the_stage_its_catalog_serves() -> None:
    target = superset.resolve_stage_report_target(_stage_environment("aws-provider-contracts-v3.json", "uat"))

    assert target.stage == "uat"


def test_resolve_stage_report_target_fails_closed_without_analytics() -> None:
    environ = _stage_environment("local-provider-contracts-v3.json", "dev")

    with pytest.raises(superset.ReportStageError, match="analytics disabled"):
        superset.resolve_stage_report_target(environ, stage="dev")


def test_resolve_stage_report_target_rejects_another_stages_exported_query_uri() -> None:
    """A shell still carrying `olf contracts env` output for another stage.

    `build_contract_env` honours a caller-set
    OPENLAKEFORGE_QUERY_SQLALCHEMY_URI even when the contract disagrees, so
    without this check a PROD import would build its bundle against the DEV
    catalog and serve DEV Gold from Superset PROD.
    """
    stale = _stage_environment("aws-provider-contracts-v3.json", "dev")["OPENLAKEFORGE_QUERY_SQLALCHEMY_URI"]
    environ = _stage_environment(
        "aws-provider-contracts-v3.json", "prod", base={"OPENLAKEFORGE_QUERY_SQLALCHEMY_URI": stale}
    )

    with pytest.raises(superset.ReportStageError, match="addresses catalog 'lakehouse_dev'.*serves 'lakehouse_prod'"):
        superset.resolve_stage_report_target(environ, stage="prod")


def test_resolve_stage_report_target_rejects_an_unhydrated_environment() -> None:
    with pytest.raises(superset.ReportStageError, match="OPENLAKEFORGE_KUBE_NAMESPACE"):
        superset.resolve_stage_report_target({}, stage="dev")


def test_bundle_identity_from_source_dir() -> None:
    identity = superset.bundle_identity("lakehouse_code/dashboards/superset/order_revenue")
    assert identity.root == "order_revenue_superset_bundle"
    assert identity.name == "order_revenue_superset_bundle.zip"


def test_validate_report_registry_rejects_declared_mounted_mismatch(tmp_path: Path) -> None:
    mounted = tmp_path / "lakehouse_code/dashboards/superset/mounted"
    mounted.mkdir(parents=True)
    (mounted / "metadata.yaml").write_text("type: assets\n")

    with pytest.raises(RuntimeError, match="declared but not mounted.*mounted but not declared"):
        superset.validate_report_registry(
            tmp_path,
            ["lakehouse_code/dashboards/superset/declared"],
        )


def test_build_report_bundle_rewrites_database_uri(tmp_path: Path) -> None:
    source = tmp_path / "report"
    (source / "databases").mkdir(parents=True)
    (source / "dashboards").mkdir()
    (source / "databases" / "trino.yaml").write_text(
        "database_name: trino\nsqlalchemy_uri: trino://old@host:8080/iceberg\n"
    )
    (source / "dashboards" / "d.yaml").write_text("dashboard_title: X\n")
    (source / "README.md").write_text("ignored")

    bundle_path = tmp_path / "bundle.zip"
    superset.build_report_bundle(source, bundle_path, "my_bundle", "trino://superset@trino:8080/iceberg")

    with ZipFile(bundle_path) as bundle:
        names = set(bundle.namelist())
        assert names == {"my_bundle/databases/trino.yaml", "my_bundle/dashboards/d.yaml"}
        db = bundle.read("my_bundle/databases/trino.yaml").decode()
        assert "sqlalchemy_uri: trino://superset@trino:8080/iceberg" in db
        assert "old@host" not in db


def test_build_report_bundle_prefixes_dataset_schemas_when_given_a_prefix(tmp_path: Path) -> None:
    source = tmp_path / "report"
    (source / "databases").mkdir(parents=True)
    (source / "datasets" / "OpenLakeForge_Trino").mkdir(parents=True)
    (source / "dashboards").mkdir()
    (source / "databases" / "trino.yaml").write_text(
        "database_name: trino\nsqlalchemy_uri: trino://old@host:8080/iceberg\n"
    )
    (source / "datasets" / "OpenLakeForge_Trino" / "mart_x.yaml").write_text(
        "table_name: mart_x\nschema: order_revenue_gold\nuuid: abc\n"
    )
    (source / "dashboards" / "d.yaml").write_text("dashboard_title: X\n")

    bundle_path = tmp_path / "bundle.zip"
    superset.build_report_bundle(
        source, bundle_path, "my_bundle", "trino://superset@trino:8080/iceberg", schema_prefix="lakehouse_dev_"
    )

    with ZipFile(bundle_path) as bundle:
        dataset = bundle.read("my_bundle/datasets/OpenLakeForge_Trino/mart_x.yaml").decode()
        assert "schema: lakehouse_dev_order_revenue_gold" in dataset


def test_build_report_bundle_leaves_dataset_schemas_alone_without_a_prefix(tmp_path: Path) -> None:
    source = tmp_path / "report"
    (source / "datasets" / "OpenLakeForge_Trino").mkdir(parents=True)
    (source / "datasets" / "OpenLakeForge_Trino" / "mart_x.yaml").write_text(
        "table_name: mart_x\nschema: order_revenue_gold\nuuid: abc\n"
    )

    bundle_path = tmp_path / "bundle.zip"
    superset.build_report_bundle(source, bundle_path, "my_bundle", "trino://superset@trino:8080/iceberg")

    with ZipFile(bundle_path) as bundle:
        dataset = bundle.read("my_bundle/datasets/OpenLakeForge_Trino/mart_x.yaml").decode()
        assert "schema: order_revenue_gold" in dataset


def test_unpack_export_bundle_replaces_managed_assets(tmp_path: Path) -> None:
    bundle_path = tmp_path / "export.zip"
    with ZipFile(bundle_path, "w") as bundle:
        bundle.writestr("root/metadata.yaml", "type: assets\n")
        bundle.writestr("root/dashboards/d.yaml", "dashboard_title: X\n")
        bundle.writestr("root/.hidden.yaml", "ignored")

    target = tmp_path / "out"
    (target / "dashboards").mkdir(parents=True)
    (target / "dashboards" / "stale.yaml").write_text("stale")

    superset.unpack_export_bundle(bundle_path, target)

    assert (target / "metadata.yaml").read_text() == "type: assets\n"
    assert (target / "dashboards" / "d.yaml").exists()
    assert not (target / "dashboards" / "stale.yaml").exists()


def test_exec_pod_python_resolves_kubectl_through_the_managed_toolchain(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_exec_pod_python` (and the other kubectl exec/copy call sites in
    this module) must resolve the executable the same way `k8s._kubectl`
    does - a bare `["kubectl", ...]` argv would fail on a clean machine
    with no host kubectl, even though the managed toolchain provisioned
    one (#127)."""
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):  # noqa: ANN001, ANN202
        calls.append(argv)

        class _Result:
            returncode = 0

        return _Result()

    monkeypatch.setattr(superset.subprocess, "run", fake_run)
    monkeypatch.setattr(k8s, "_kubectl_executable", lambda: "/managed/bin/kubectl")
    monkeypatch.setenv("KUBE_CONTEXT", "kind-openlakeforge-local")

    superset._exec_pod_python("superset-pod", "lakehouse", "print('hi')", [])

    assert calls[0][0] == "/managed/bin/kubectl"
