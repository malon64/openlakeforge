from pathlib import Path
from zipfile import ZipFile

from olf import superset


def test_bundle_identity_from_source_dir() -> None:
    identity = superset.bundle_identity("domains/sales/reports/superset/order_revenue")
    assert identity.root == "sales_order_revenue_superset_bundle"
    assert identity.name == "sales_order_revenue_superset_bundle.zip"


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
