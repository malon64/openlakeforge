from pathlib import Path
from typing import Any

import pytest
from conftest import write_dashboard_fixture, write_two_product_fixture
from openlakeforge_domain import load_domain_inventory

from olf.clients.openmetadata import OpenMetadataClient, OpenMetadataError
from olf.e2e import _assertions, _health
from olf.e2e._shell import E2EConfig, E2EError


def test_openmetadata_data_product_candidates_try_short_and_domain_names() -> None:
    seen: list[str] = []

    def request(method: str, path: str, **_kwargs) -> dict[str, Any]:
        seen.append(path)
        if path.endswith("/sales.sales_order_revenue"):
            return {}
        raise OpenMetadataError(f"{method} {path} failed with HTTP 404: not found")

    client = OpenMetadataClient("http://openmetadata")
    client.request = request

    assert _assertions._first_existing_data_product(client, ("sales_order_revenue", "sales.sales_order_revenue")) == (
        "sales.sales_order_revenue"
    )
    assert seen == [
        "/api/v1/dataProducts/name/sales_order_revenue",
        "/api/v1/dataProducts/name/sales.sales_order_revenue",
    ]


def test_two_product_fixture_repo_drives_exactly_its_own_jobs_dashboards_and_marts(tmp_path: Path) -> None:
    """A descriptor change alone must move e2e's discovered work, per issue #39."""
    write_two_product_fixture(tmp_path)
    inventory = load_domain_inventory(tmp_path)
    fixture_cfg = E2EConfig(
        env="local",
        suite="full",
        namespace="lakehouse",
        kube_context="kind-openlakeforge-local",
        repo_root=tmp_path,
        foundation_terraform_dir=tmp_path / "foundation",
        contract_terraform_dir=tmp_path / "contract",
        inventory=inventory,
    )

    assert fixture_cfg.inventory.job_names == ("widgets_alpha_pipeline", "widgets_beta_pipeline")
    assert fixture_cfg.inventory.gold_mart_names == (
        "widgets_alpha_gold.mart_alpha_summary",
        "widgets_beta_gold.mart_beta_summary",
    )

    # Dashboard identity comes from the exported Superset YAML, not an
    # asset_prefix/displayName convention — a title/slug that doesn't match
    # that convention, and a product with two dashboards, must both work.
    write_dashboard_fixture(
        tmp_path,
        inventory.default_product.report_source_dir,
        "Overview_1.yaml",
        slug="widgets-alpha-overview",
        title="Widgets Alpha Overview Board",
    )
    beta_product = next(product for product in inventory.products if product.id == "beta")
    write_dashboard_fixture(
        tmp_path, beta_product.report_source_dir, "Beta_Main_1.yaml", slug="widgets-beta-main", title="Widgets Beta"
    )
    write_dashboard_fixture(
        tmp_path, beta_product.report_source_dir, "Beta_Detail_1.yaml", slug="widgets-beta-detail", title="Beta Detail"
    )

    expected = _assertions.discovered_dashboards(fixture_cfg)

    assert expected == {
        "widgets-alpha-overview": "Widgets Alpha Overview Board",
        "widgets-beta-main": "Widgets Beta",
        "widgets-beta-detail": "Beta Detail",
    }
    _assertions.assert_superset_dashboards(
        [
            {"slug": "widgets-alpha-overview", "dashboard_title": "Widgets Alpha Overview Board"},
            {"slug": "widgets-beta-main", "dashboard_title": "Widgets Beta"},
            {"slug": "widgets-beta-detail", "dashboard_title": "Beta Detail"},
        ],
        expected,
    )
    # Pipeline Job attempts are assessed by the launch-and-poll assertion, not
    # by the initial platform readiness check. Historical failed attempts must
    # not prevent a later suite from starting.
    assert _health.classify_pod_health(
        {
            "items": [
                {
                    "metadata": {
                        "name": "widgets-alpha-run-pod",
                        "ownerReferences": [{"kind": "Job", "name": "widgets_alpha_pipeline-run"}],
                    },
                    "status": {"phase": "Failed"},
                }
            ]
        }
    ) == []


def test_discovered_dashboards_rejects_a_product_with_no_dashboard_export(tmp_path: Path) -> None:
    """A missing dashboard export must fail loudly, not pass by contributing nothing."""
    write_two_product_fixture(tmp_path)
    inventory = load_domain_inventory(tmp_path)
    fixture_cfg = E2EConfig(
        env="local",
        suite="full",
        namespace="lakehouse",
        kube_context="kind-openlakeforge-local",
        repo_root=tmp_path,
        foundation_terraform_dir=tmp_path / "foundation",
        contract_terraform_dir=tmp_path / "contract",
        inventory=inventory,
    )
    # Only the default (first) product exports a dashboard; the second has none.
    write_dashboard_fixture(
        tmp_path,
        inventory.default_product.report_source_dir,
        "Overview_1.yaml",
        slug="widgets-alpha-overview",
        title="Widgets Alpha Overview Board",
    )

    with pytest.raises(E2EError, match="exports no Superset dashboards"):
        _assertions.discovered_dashboards(fixture_cfg)


def test_discovered_dashboards_accepts_yml_suffixed_exports(tmp_path: Path) -> None:
    """superset.build_report_bundle packages both .yaml and .yml — discovery must match."""
    write_two_product_fixture(tmp_path)
    inventory = load_domain_inventory(tmp_path)
    fixture_cfg = E2EConfig(
        env="local",
        suite="full",
        namespace="lakehouse",
        kube_context="kind-openlakeforge-local",
        repo_root=tmp_path,
        foundation_terraform_dir=tmp_path / "foundation",
        contract_terraform_dir=tmp_path / "contract",
        inventory=inventory,
    )
    beta_product = next(product for product in inventory.products if product.id == "beta")
    write_dashboard_fixture(
        tmp_path,
        inventory.default_product.report_source_dir,
        "Overview_1.yaml",
        slug="widgets-alpha-overview",
        title="Widgets Alpha Overview Board",
    )
    write_dashboard_fixture(
        tmp_path, beta_product.report_source_dir, "Beta_Main_1.yml", slug="widgets-beta-main", title="Widgets Beta"
    )

    assert _assertions.discovered_dashboards(fixture_cfg) == {
        "widgets-alpha-overview": "Widgets Alpha Overview Board",
        "widgets-beta-main": "Widgets Beta",
    }
