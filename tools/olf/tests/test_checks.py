from __future__ import annotations

from pathlib import Path

import pytest
import typer

from olf import dashboard_checks
from olf.commands import checks


def test_dashboard_validation_accepts_the_repository_assets() -> None:
    root = Path(__file__).resolve().parents[3]

    assert dashboard_checks.validate_superset_assets(root) == []


def test_dashboard_validation_rejects_unknown_dataset_column(tmp_path: Path) -> None:
    dataset = tmp_path / "lakehouse_code/dashboards/superset/example/datasets/trino/orders.yaml"
    chart = tmp_path / "lakehouse_code/dashboards/superset/example/charts/orders.yaml"
    dataset.parent.mkdir(parents=True)
    chart.parent.mkdir(parents=True)
    dataset.write_text(
        "uuid: orders\ncolumns:\n  - column_name: order_date\nmetrics:\n  - metric_name: sum__sales\n"
    )
    chart.write_text(
        "viz_type: echarts_timeseries_line\ndataset_uuid: orders\nparams:\n"
        "  x_axis: missing_column\n  metrics:\n    - sum__sales\n"
    )

    errors = dashboard_checks.validate_superset_assets(tmp_path)

    assert any("missing_column" in error for error in errors)


def test_superset_render_requires_ephemeral_reports_volume_and_mount() -> None:
    rendered = """\
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      volumes:
        - name: superset-reports
          emptyDir:
            sizeLimit: 1Gi
      containers:
        - name: superset
          volumeMounts:
            - name: superset-reports
              mountPath: /app/openlakeforge/reports
"""

    checks._validate_superset_render(rendered)


def test_superset_render_rejects_reports_pvc() -> None:
    rendered = """\
kind: PersistentVolumeClaim
metadata:
  name: superset-reports
"""

    with pytest.raises(typer.Exit):
        checks._validate_superset_render(rendered)


def test_project_code_cache_digest_includes_source_paths_and_contents(tmp_path: Path) -> None:
    source = tmp_path / "domain" / "model.py"
    source.parent.mkdir()
    source.write_text("value = 1\n")
    first = checks._source_tree_digest(source.parent)
    source.write_text("value = 2\n")

    assert checks._source_tree_digest(source.parent) != first


def test_project_code_rejects_unsupported_python_before_building_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(checks.sys, "version_info", (3, 13, 0, "final", 0))

    with pytest.raises(typer.Exit):
        checks.project_code(str(tmp_path))
