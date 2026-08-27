"""Shared test fixtures used across the olf.e2e test files.

`e2e_cfg`/`E2E_REPO_ROOT`/`E2E_INVENTORY` are used by most of the
`test_e2e_*.py` files (mirroring `olf/e2e/`'s capability submodules), so they
live here once rather than duplicated per file.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openlakeforge_domain import inventory_for

from olf.e2e._shell import E2EConfig, Environment, Suite


@pytest.fixture(autouse=True)
def _isolate_toolchain(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """Every test defaults to host-mode executable resolution with an
    isolated `OLF_HOME`.

    `olf.tooling.resolver.build_resolver()` provisions managed tools (#127)
    over the network into `OLF_HOME` (`~/.openlakeforge` by default) unless
    told otherwise. Without this guard, any test that exercises a real
    (unmocked) `kubectl`/`terraform` execution path - directly or via
    `olf.k8s`/`olf.e2e._shell` - would silently download real Terraform/
    kubectl/helm/kind binaries onto the network and into the developer's
    actual home directory. Tests that specifically exercise managed-mode
    resolution (`tests/test_toolchain_*.py`) override both variables
    themselves with an injected fake downloader.
    """
    monkeypatch.setenv("OLF_TOOLCHAIN_MODE", "host")
    monkeypatch.setenv("OLF_HOME", str(tmp_path_factory.mktemp("olf-home")))

E2E_REPO_ROOT = Path(__file__).resolve().parents[3]
E2E_INVENTORY = inventory_for(E2E_REPO_ROOT)


def e2e_cfg(tmp_path: Path, env: Environment = "local", suite: Suite = "full") -> E2EConfig:
    return E2EConfig(
        env=env,
        suite=suite,
        namespace="lakehouse",
        kube_context="kind-openlakeforge-local",
        repo_root=tmp_path,
        distribution_root=tmp_path,
        foundation_terraform_dir=tmp_path / "foundation",
        contract_terraform_dir=tmp_path / "contract",
        inventory=E2E_INVENTORY,
        aws_region="eu-west-1" if env == "aws" else None,
    )


def write_two_product_fixture(root: Path, *, dashboards: tuple[tuple[str, str], ...] = ()) -> None:
    """A minimal two-product, single-domain v1alpha3 descriptor for isolation
    tests. ``dashboards`` is a sequence of ``(dashboard_name, product_id)``
    pairs to declare in the top-level ``dashboards:`` list; the caller still
    has to write the actual Superset export via ``write_dashboard_fixture``.
    """
    lakehouse_dir = root / "lakehouse_code"
    source_dir = lakehouse_dir / "bronze" / "widgets_source"
    source_dir.mkdir(parents=True)
    (source_dir / "source.yaml").write_text(
        """\
apiVersion: openlakeforge.io/v1alpha3
kind: Source
name: widgets_source
displayName: Widgets Source
description: Widgets bronze source fixture.
status: planned
resources:
  - name: source
""",
        encoding="utf-8",
    )
    dashboards_yaml = ""
    if dashboards:
        entries = "\n".join(f"  - name: {name}\n    products: [{product}]" for name, product in dashboards)
        dashboards_yaml = f"dashboards:\n{entries}\n"
    (lakehouse_dir / "lakehouse.yaml").write_text(
        f"""\
apiVersion: openlakeforge.io/v1alpha3
kind: Lakehouse
name: test
displayName: Test
description: Widgets fixture lakehouse.
status: planned
sources:
  - widgets_source
domains:
  - name: widgets
    displayName: Widgets
    description: Widgets domain fixture.
    status: planned
    silver_tables:
      tables:
        - {{name: source, source: widgets_source, resource: source}}
    products:
      - id: widgets_alpha
        displayName: Widgets Alpha
        description: Alpha product.
        status: planned
        silver_inputs: [source]
        gold_tables:
          tables:
            - name: mart_alpha_summary
      - id: widgets_beta
        displayName: Widgets Beta
        description: Beta product.
        status: planned
        silver_inputs: [source]
        gold_tables:
          tables:
            - name: mart_beta_summary
{dashboards_yaml}""",
        encoding="utf-8",
    )


def write_dashboard_fixture(repo_root: Path, report_source_dir: str, file_name: str, *, slug: str, title: str) -> None:
    report_dir = repo_root / report_source_dir
    dashboards_dir = report_dir / "dashboards"
    dashboards_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "metadata.yaml").write_text("type: assets\n", encoding="utf-8")
    (dashboards_dir / file_name).write_text(f"dashboard_title: {title}\nslug: {slug}\n", encoding="utf-8")
