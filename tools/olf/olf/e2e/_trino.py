"""Trino/Polaris catalog, namespace, and table-count assertions."""

from __future__ import annotations

from openlakeforge_domain import Product

from olf import log
from olf.e2e._shell import E2EConfig, E2EError, kubectl

POLARIS_POD_SELECTOR = "app.kubernetes.io/name=polaris,app.kubernetes.io/instance=polaris"
POLARIS_RESTART_TIMEOUT_SECONDS = 300


def check_trino_catalog(cfg: E2EConfig) -> None:
    log.step("Checking Trino catalogs...")
    catalogs = trino_query(cfg, "SHOW CATALOGS")
    if "iceberg" not in set(catalogs.splitlines()):
        raise E2EError("Trino did not expose the iceberg catalog.")


def check_catalog_namespaces(cfg: E2EConfig) -> None:
    """Verify Polaris exposes every descriptor-derived namespace through Trino."""
    log.step("Checking Polaris namespaces through Trino...")
    namespaces = set(trino_query(cfg, "SHOW SCHEMAS FROM iceberg").splitlines())
    expected = cfg.inventory.silver_namespace_names | cfg.inventory.gold_namespace_names
    missing = sorted(expected - namespaces)
    if missing:
        raise E2EError("Polaris is missing descriptor-derived namespaces: " + ", ".join(missing))


def _schema_in_list(schema_names: frozenset[str]) -> str:
    return ", ".join(f"'{name}'" for name in sorted(schema_names))


def check_trino_tables_and_marts(cfg: E2EConfig) -> None:
    check_trino_catalog(cfg)
    log.step("Checking Silver and Gold table counts...")
    silver_count = trino_scalar(
        cfg,
        "SELECT count(*) FROM iceberg.information_schema.tables "
        f"WHERE table_schema IN ({_schema_in_list(cfg.inventory.silver_namespace_names)})",
    )
    gold_count = trino_scalar(
        cfg,
        "SELECT count(*) FROM iceberg.information_schema.tables "
        f"WHERE table_schema IN ({_schema_in_list(cfg.inventory.gold_namespace_names)})",
    )
    assert_scalar_equals(silver_count, str(cfg.inventory.silver_table_count), "Silver table count")
    assert_scalar_equals(gold_count, str(cfg.inventory.gold_table_count), "Gold mart count")

    for mart in cfg.inventory.gold_mart_names:
        count = trino_scalar(cfg, f"SELECT count(*) FROM iceberg.{mart}")
        if int(count) <= 0:
            raise E2EError(f"expected iceberg.{mart} to contain rows, got {count}")


def check_trino_product_tables_and_marts(cfg: E2EConfig, product: Product) -> None:
    """Verify the selected smoke product reached populated Silver and Gold tables."""
    check_trino_catalog(cfg)
    log.step(f"Checking Silver and Gold tables for {product.id}...")
    silver_count = trino_scalar(
        cfg,
        "SELECT count(*) FROM iceberg.information_schema.tables "
        f"WHERE table_schema = '{product.silver_namespace}'",
    )
    gold_count = trino_scalar(
        cfg,
        "SELECT count(*) FROM iceberg.information_schema.tables "
        f"WHERE table_schema = '{product.gold_namespace}'",
    )
    assert_scalar_equals(silver_count, str(len(product.silver_tables)), f"{product.id} Silver table count")
    assert_scalar_equals(gold_count, str(len(product.gold_tables)), f"{product.id} Gold mart count")
    for mart in product.gold_mart_names:
        count = trino_scalar(cfg, f"SELECT count(*) FROM iceberg.{mart}")
        if int(count) <= 0:
            raise E2EError(f"expected iceberg.{mart} to contain rows, got {count}")


def check_polaris_restart_recovery(cfg: E2EConfig) -> None:
    """Verify that Polaris keeps table identity after its pod is recreated."""
    log.step("Checking Polaris restart recovery...")
    pod_name = kubectl(
        cfg,
        [
            "get",
            "pods",
            "-n",
            cfg.namespace,
            "-l",
            POLARIS_POD_SELECTOR,
            "-o",
            "jsonpath={.items[0].metadata.name}",
        ],
        capture=True,
    ).strip()
    if not pod_name:
        raise E2EError("could not find the Polaris pod to restart.")

    kubectl(cfg, ["delete", "pod", pod_name, "-n", cfg.namespace])
    kubectl(
        cfg,
        [
            "wait",
            "--for=condition=Ready",
            "pod",
            "-n",
            cfg.namespace,
            "-l",
            POLARIS_POD_SELECTOR,
            f"--timeout={POLARIS_RESTART_TIMEOUT_SECONDS}s",
        ],
    )
    check_trino_tables_and_marts(cfg)


def trino_scalar(cfg: E2EConfig, sql: str) -> str:
    return parse_trino_scalar(trino_query(cfg, sql))


def trino_query(cfg: E2EConfig, sql: str) -> str:
    output = kubectl(
        cfg,
        [
            "exec",
            "-n",
            cfg.namespace,
            "deploy/trino-coordinator",
            "--",
            "trino",
            "--output-format",
            "CSV_UNQUOTED",
            "--execute",
            sql,
        ],
        capture=True,
        retry_transient=True,
    )
    return output


def parse_trino_scalar(output: str) -> str:
    values = [line.strip().replace("\r", "") for line in output.splitlines() if line.strip()]
    if not values:
        raise E2EError("Trino query returned no scalar value.")
    return values[-1]


def assert_scalar_equals(actual: str, expected: str, label: str) -> None:
    if actual != expected:
        raise E2EError(f"expected {label} {expected}, got {actual}")
