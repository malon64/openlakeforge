from __future__ import annotations

import boto3
import pytest
from botocore.stub import Stubber

from olf import glue
from olf.inventory import CatalogNamespace


def make_client() -> tuple[glue.GlueClient, Stubber]:
    boto_client = boto3.client(
        "glue",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        aws_session_token="testing",
    )
    stubber = Stubber(boto_client)
    client = glue.GlueClient(glue.GlueConfig(catalog_id="123456789012", region="us-east-1"), _client=boto_client)
    return client, stubber


def test_list_namespaces_paginates_and_maps_location_uri() -> None:
    client, stubber = make_client()
    stubber.add_response(
        "get_databases",
        {
            "DatabaseList": [{"Name": "sales_silver", "LocationUri": "s3://silver/sales_silver/"}],
            "NextToken": "page2",
        },
        {"CatalogId": "123456789012"},
    )
    stubber.add_response(
        "get_databases",
        {"DatabaseList": [{"Name": "sales_gold", "LocationUri": "s3://gold/sales_gold/"}]},
        {"CatalogId": "123456789012", "NextToken": "page2"},
    )

    with stubber:
        assert client.list_namespaces() == {
            "sales_silver": "s3://silver/sales_silver/",
            "sales_gold": "s3://gold/sales_gold/",
        }


def test_list_namespaces_defaults_missing_location_to_empty_string() -> None:
    client, stubber = make_client()
    stubber.add_response(
        "get_databases",
        {"DatabaseList": [{"Name": "no_location"}]},
        {"CatalogId": "123456789012"},
    )

    with stubber:
        assert client.list_namespaces() == {"no_location": ""}


def test_create_namespace_issues_the_expected_database_input() -> None:
    client, stubber = make_client()
    namespace = CatalogNamespace(name="sales_silver", location="s3://silver/sales_silver/")
    stubber.add_response(
        "create_database",
        {},
        {
            "CatalogId": "123456789012",
            "DatabaseInput": {
                "Name": "sales_silver",
                "LocationUri": "s3://silver/sales_silver/",
                "Description": "OpenLakeForge 123456789012 sales_silver Iceberg namespace",
            },
        },
    )

    with stubber:
        client.create_namespace(namespace)

    stubber.assert_no_pending_responses()


def test_create_namespace_tolerates_already_existing() -> None:
    client, stubber = make_client()
    namespace = CatalogNamespace(name="sales_silver", location="s3://silver/sales_silver/")
    stubber.add_client_error("create_database", service_error_code="AlreadyExistsException")

    with stubber:
        client.create_namespace(namespace)  # must not raise


def test_update_namespace_location_issues_update_database() -> None:
    client, stubber = make_client()
    namespace = CatalogNamespace(name="sales_silver", location="s3://new-silver/sales_silver/")
    stubber.add_response(
        "update_database",
        {},
        {
            "CatalogId": "123456789012",
            "Name": "sales_silver",
            "DatabaseInput": {
                "Name": "sales_silver",
                "LocationUri": "s3://new-silver/sales_silver/",
                "Description": "OpenLakeForge 123456789012 sales_silver Iceberg namespace",
            },
        },
    )

    with stubber:
        client.update_namespace_location(namespace)

    stubber.assert_no_pending_responses()


def test_drop_namespace_deletes_an_empty_database() -> None:
    client, stubber = make_client()
    stubber.add_response(
        "get_tables",
        {"TableList": []},
        {"CatalogId": "123456789012", "DatabaseName": "retired_silver", "MaxResults": 1},
    )
    stubber.add_response(
        "delete_database", {}, {"CatalogId": "123456789012", "Name": "retired_silver"}
    )

    with stubber:
        client.drop_namespace("retired_silver")

    stubber.assert_no_pending_responses()


def test_drop_namespace_refuses_a_non_empty_database() -> None:
    """Glue's DeleteDatabase cascades to every table inside rather than
    refusing, unlike Polaris's 409-on-non-empty-namespace. This GetTables
    guard is what reproduces that safety property for --prune."""
    client, stubber = make_client()
    stubber.add_response(
        "get_tables",
        {"TableList": [{"Name": "orders"}]},
        {"CatalogId": "123456789012", "DatabaseName": "sales_silver", "MaxResults": 1},
    )

    with stubber, pytest.raises(glue.GlueError, match="still holds tables"):
        client.drop_namespace("sales_silver")

    stubber.assert_no_pending_responses()


def test_drop_namespace_tolerates_already_missing() -> None:
    client, stubber = make_client()
    stubber.add_response(
        "get_tables",
        {"TableList": []},
        {"CatalogId": "123456789012", "DatabaseName": "gone", "MaxResults": 1},
    )
    stubber.add_client_error("delete_database", service_error_code="EntityNotFoundException")

    with stubber:
        client.drop_namespace("gone")  # must not raise
