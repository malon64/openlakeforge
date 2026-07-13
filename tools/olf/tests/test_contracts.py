import json
from pathlib import Path

import pytest

from olf.contracts import build_contract_env, render_shell_exports

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def test_defaults_without_contracts_match_local_profile() -> None:
    exports, unsets = build_contract_env({}, None)
    assert exports["OPENLAKEFORGE_STORAGE_IMPLEMENTATION"] == "storage.s3_compatible.seaweedfs"
    assert exports["OPENLAKEFORGE_STORAGE_ENDPOINT"] == "http://seaweedfs-s3:8333"
    assert exports["OPENLAKEFORGE_CATALOG_PROVIDER"] == "polaris"
    assert exports["OPENLAKEFORGE_CATALOG_WAREHOUSE"] == "lakehouse_dev"
    assert exports["OPENLAKEFORGE_ARTIFACT_BASE_URI"] == "s3://openlakeforge-ops"
    assert exports["OPENLAKEFORGE_CATALOG_DATABASE_FQN"] == "polaris.lakehouse_dev"
    assert json.loads(exports["OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON"]) == {
        "sales_order_revenue": "polaris.lakehouse_dev.sales_order_revenue_silver",
        "sales_customer_health": "polaris.lakehouse_dev.sales_customer_health_silver",
        "supply_chain_inventory_reliability": (
            "polaris.lakehouse_dev.supply_chain_inventory_reliability_silver"
        ),
    }
    assert json.loads(exports["OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON"]) == {
        "sales_order_revenue": "polaris.lakehouse_dev.sales_order_revenue_gold",
        "sales_customer_health": "polaris.lakehouse_dev.sales_customer_health_gold",
        "supply_chain_inventory_reliability": (
            "polaris.lakehouse_dev.supply_chain_inventory_reliability_gold"
        ),
    }
    assert exports["OPENMETADATA_CATALOG_SERVICE"] == "polaris"
    assert exports["OPENLAKEFORGE_STORAGE_OM_SERVICE"] == "seaweedfs"
    assert exports["OPENLAKEFORGE_STORAGE_DISPLAY_NAME"] == "SeaweedFS S3"
    assert exports["AWS_ENDPOINT_URL_S3"] == "http://seaweedfs-s3:8333"
    assert exports["AWS_ALLOW_HTTP"] == "true"
    assert exports["CODE_BUCKET_NAME"] == "openlakeforge-ops"
    assert (
        exports["OPENLAKEFORGE_QUERY_SQLALCHEMY_URI"] == "trino://superset@trino:8080/iceberg"
    )
    assert unsets == []


def test_local_contracts_apply_seaweedfs_values() -> None:
    exports, unsets = build_contract_env({}, load_fixture("local-provider-contracts.json"))
    assert exports["OPENLAKEFORGE_STORAGE_PROVIDER"] == "local"
    assert exports["OPENLAKEFORGE_STORAGE_S3_SERVICE_PORT"] == "8333"
    assert exports["OPENLAKEFORGE_STORAGE_PATH_STYLE_ACCESS"] == "true"
    assert exports["OPENLAKEFORGE_QUERY_TRINO_HOST"] == "trino"
    assert exports["OPENLAKEFORGE_QUERY_TRINO_PORT"] == "8080"
    assert exports["OPENLAKEFORGE_CATALOG_DATABASE_FQN"] == "polaris.lakehouse_dev"
    assert json.loads(exports["OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON"]) == {
        "sales_order_revenue": "polaris.lakehouse_dev.sales_order_revenue_silver",
        "sales_customer_health": "polaris.lakehouse_dev.sales_customer_health_silver",
        "supply_chain_inventory_reliability": (
            "polaris.lakehouse_dev.supply_chain_inventory_reliability_silver"
        ),
    }
    assert json.loads(exports["OPENLAKEFORGE_CATALOG_SILVER_NAMESPACES_JSON"]) == {
        "sales_order_revenue": "sales_order_revenue_silver"
    }
    assert unsets == []


def test_aws_contracts_blank_local_only_fields_and_derive_glue_fqns() -> None:
    exports, unsets = build_contract_env({}, load_fixture("aws-provider-contracts.json"))
    # storage.aws_s3 blanks S3-compatible endpoint plumbing
    assert exports["OPENLAKEFORGE_STORAGE_ENDPOINT"] == ""
    assert exports["OPENLAKEFORGE_STORAGE_CREDENTIALS_SECRET_NAME"] == ""
    assert exports["OPENLAKEFORGE_STORAGE_PATH_STYLE_ACCESS"] == "false"
    assert "AWS_ENDPOINT_URL_S3" in unsets
    assert "OPENLAKEFORGE_DUCKDB_S3_ENDPOINT" in unsets
    # glue catalog blanks Polaris OAuth plumbing
    assert exports["OPENLAKEFORGE_CATALOG_TOKEN_URI"] == ""
    assert exports["OPENLAKEFORGE_CATALOG_OAUTH_SCOPE"] == ""
    assert exports["OPENLAKEFORGE_CATALOG_FLOE_CREDENTIALS_SECRET_NAME"] == ""
    assert exports["OPENLAKEFORGE_CATALOG_FLOE_CLIENT_ID_KEY"] == ""
    assert exports["OPENLAKEFORGE_CATALOG_FLOE_CLIENT_SECRET_KEY"] == ""
    assert exports["OPENLAKEFORGE_CATALOG_DBT_CREDENTIALS_SECRET_NAME"] == ""
    assert exports["OPENLAKEFORGE_CATALOG_DBT_CLIENT_ID_KEY"] == ""
    assert exports["OPENLAKEFORGE_CATALOG_DBT_CLIENT_SECRET_KEY"] == ""
    assert exports["OPENLAKEFORGE_CATALOG_WAREHOUSE"] == "lakehouse_dev"
    assert exports["OPENLAKEFORGE_CATALOG_GLUE_REST_URI"] == "https://glue.eu-west-1.amazonaws.com/iceberg"
    assert exports["OPENLAKEFORGE_CATALOG_GLUE_REST_WAREHOUSE"] == "123456789012"
    assert exports["OPENLAKEFORGE_CATALOG_GLUE_WAREHOUSE_PREFIX"] == "warehouse/iceberg"
    # OpenMetadata mappings follow the Glue provider
    assert exports["OPENLAKEFORGE_CATALOG_DATABASE_FQN"] == "aws_glue.lakehouse_dev"
    assert json.loads(exports["OPENLAKEFORGE_CATALOG_SILVER_SCHEMA_FQNS_JSON"]) == {
        "sales_order_revenue": "aws_glue.lakehouse_dev.sales_order_revenue_silver"
    }
    assert json.loads(exports["OPENLAKEFORGE_CATALOG_GOLD_SCHEMA_FQNS_JSON"]) == {
        "sales_order_revenue": "aws_glue.lakehouse_dev.sales_order_revenue_gold"
    }
    assert exports["OPENMETADATA_CATALOG_SERVICE"] == "aws_glue"
    assert exports["OPENLAKEFORGE_STORAGE_OM_SERVICE"] == "aws_s3"
    assert exports["OPENLAKEFORGE_STORAGE_DISPLAY_NAME"] == "AWS S3"
    assert exports["CODE_BUCKET_NAME"] == "openlakeforge-poc-ops"
    # AWS_REGION must follow the resolved contract storage region, not the
    # us-east-1 default from the pre-contract pass.
    assert exports["AWS_REGION"] == "eu-west-1"
    assert exports["AWS_DEFAULT_REGION"] == "eu-west-1"


def test_caller_exported_aws_region_is_preserved() -> None:
    exports, _ = build_contract_env(
        {"AWS_REGION": "us-west-2"}, load_fixture("aws-provider-contracts.json")
    )
    assert exports["AWS_REGION"] == "us-west-2"


def test_caller_environment_wins_for_user_settable_variables() -> None:
    base = {
        "OPENLAKEFORGE_QUERY_SQLALCHEMY_URI": "trino://custom@example:8080/iceberg",
        "OPENMETADATA_CATALOG_SERVICE": "custom_service",
        "OPENLAKEFORGE_STORAGE_OM_SERVICE": "custom_storage",
        "OPENLAKEFORGE_STORAGE_DISPLAY_NAME": "Custom S3",
    }
    exports, _ = build_contract_env(base, load_fixture("aws-provider-contracts.json"))
    assert "OPENLAKEFORGE_QUERY_SQLALCHEMY_URI" not in exports
    assert "OPENMETADATA_CATALOG_SERVICE" not in exports
    assert "OPENLAKEFORGE_STORAGE_OM_SERVICE" not in exports
    assert "OPENLAKEFORGE_STORAGE_DISPLAY_NAME" not in exports


def test_contract_values_override_inherited_environment() -> None:
    base = {"OPENLAKEFORGE_STORAGE_BRONZE_BUCKET": "stale-bucket"}
    exports, _ = build_contract_env(base, load_fixture("aws-provider-contracts.json"))
    assert exports["OPENLAKEFORGE_STORAGE_BRONZE_BUCKET"] == "openlakeforge-poc-bronze"


def test_namespace_env_feeds_kube_namespace_default() -> None:
    exports, _ = build_contract_env({"NAMESPACE": "custom-ns"}, None)
    assert exports["OPENLAKEFORGE_KUBE_NAMESPACE"] == "custom-ns"


@pytest.mark.parametrize("fixture", ["local-provider-contracts.json", "aws-provider-contracts.json"])
def test_render_shell_exports_are_evaluable_lines(fixture: str) -> None:
    exports, unsets = build_contract_env({}, load_fixture(fixture))
    output = render_shell_exports(exports, unsets)
    for line in output.splitlines():
        assert line.startswith("export ") or line.startswith("unset ")
