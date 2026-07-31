"""Unit tests for libs.product_dagster's fail-closed artifact-revision check.

Follows the sys.path pattern test_dbt.py already uses to reach the top-level
`libs` package from tools/olf/tests. Unlike libs/dbt, libs/product_dagster
imports dagster/dagster-dbt/dagster-floe, which are project-code image
dependencies, not tools/olf ones (tools/olf/pyproject.toml only has boto3,
PyYAML, requests, typer) -- `uv run --project tools/olf pytest` does not have
them installed, so this module is skipped there rather than failing collection.
Verified locally against a venv built from images/project-code's actual
dependencies (dagster-floe[kubernetes]==0.2.5, dagster-dbt): all 6 tests pass.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

pytest.importorskip("dagster")

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from libs.product_dagster import ArtifactRevisionError, ProductDefinitionSpec, _validate_artifact_revision  # noqa: E402


def _spec() -> ProductDefinitionSpec:
    return ProductDefinitionSpec(
        domain="sales",
        product="order_revenue",
        asset_prefix="sales_order_revenue",
        entities=(),
        gold_assets=(),
        domain_dir=Path("domains/sales"),
        bronze_loader=lambda: {},
    )


MANIFEST_URI = "s3://ops/floe/manifests/sales/order_revenue/order_revenue.manifest.json"
ENTRY_KEY = "floe/manifests/sales/order_revenue/order_revenue.manifest.json"


def test_manual_revision_skips_validation_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENLAKEFORGE_FLOE_MANIFEST_REVISION", raising=False)
    # No image label, no base URI -- would fail every check below if not skipped.
    _validate_artifact_revision(_spec(), MANIFEST_URI, b"anything")


def test_image_label_mismatch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_REVISION", "sha256:expected")
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT", "sha256:different")
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI", "s3://ops/floe/manifests")
    import libs.product_dagster as pd

    manifest_bytes = b"manifest-content"
    monkeypatch.setattr(
        pd,
        "read_text_uri",
        lambda uri: __import__("json").dumps(
            {"revision": "sha256:expected", "entries": {ENTRY_KEY: hashlib.sha256(manifest_bytes).hexdigest()}}
        ),
    )
    with pytest.raises(ArtifactRevisionError, match="does not match the runtime environment"):
        pd._validate_artifact_revision(_spec(), MANIFEST_URI, manifest_bytes)


def test_missing_product_in_sidecar_entries_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A product dropped from the generated set whose old bucket object survives
    must not be silently accepted just because it is absent from `entries`.
    """
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_REVISION", "sha256:expected")
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT", "sha256:expected")
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI", "s3://ops/floe/manifests")
    import libs.product_dagster as pd

    monkeypatch.setattr(
        pd,
        "read_text_uri",
        lambda uri: __import__("json").dumps({"revision": "sha256:expected", "entries": {}}),
    )
    with pytest.raises(ArtifactRevisionError, match="is not recorded in the published artifact revision sidecar"):
        pd._validate_artifact_revision(_spec(), MANIFEST_URI, b"manifest-content")


def test_fetched_hash_mismatch_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_REVISION", "sha256:expected")
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT", "sha256:expected")
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI", "s3://ops/floe/manifests")
    import libs.product_dagster as pd

    monkeypatch.setattr(
        pd,
        "read_text_uri",
        lambda uri: __import__("json").dumps({"revision": "sha256:expected", "entries": {ENTRY_KEY: "a" * 64}}),
    )
    with pytest.raises(ArtifactRevisionError, match="does not match the published revision's recorded hash"):
        pd._validate_artifact_revision(_spec(), MANIFEST_URI, b"unrelated-manifest-content")


def test_no_base_uri_fails_closed_instead_of_skipping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-product OPENLAKEFORGE_FLOE_MANIFEST_URI_<PRODUCT> overrides resolve
    manifest_uri but carry no way to locate the shared REVISION sidecar. Without
    a base URI to fail closed on, a non-'manual' revision would pass on
    image-label/env agreement alone even though the manifest content was never
    actually checked against anything.
    """
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_REVISION", "sha256:expected")
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT", "sha256:expected")
    monkeypatch.delenv("OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI", raising=False)
    with pytest.raises(ArtifactRevisionError, match="requires OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI"):
        _validate_artifact_revision(_spec(), MANIFEST_URI, b"manifest-content")


def test_fully_consistent_revision_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_REVISION", "sha256:expected")
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT", "sha256:expected")
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI", "s3://ops/floe/manifests")
    import libs.product_dagster as pd

    manifest_bytes = b"manifest-content"
    monkeypatch.setattr(
        pd,
        "read_text_uri",
        lambda uri: __import__("json").dumps(
            {"revision": "sha256:expected", "entries": {ENTRY_KEY: hashlib.sha256(manifest_bytes).hexdigest()}}
        ),
    )
    pd._validate_artifact_revision(_spec(), MANIFEST_URI, manifest_bytes)


def _spec_with_local_manifest(tmp_path: Path) -> ProductDefinitionSpec:
    manifest_dir = tmp_path / "contracts" / "floe" / "manifests"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "order_revenue.manifest.json").write_text('{"placeholder": true}')
    return ProductDefinitionSpec(
        domain="sales",
        product="order_revenue",
        asset_prefix="sales_order_revenue",
        entities=(),
        gold_assets=(),
        domain_dir=tmp_path,
        bronze_loader=lambda: {},
    )


def test_transient_fetch_failure_fails_closed_when_revision_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A network hiccup fetching the remote manifest must not silently
    degrade to the local/baked-in manifest while revision enforcement is
    active: _validate_artifact_revision never even runs for this attempt, so
    falling back would run Dagster on a manifest that was never checked
    against the runtime's expected revision. Once the transient failure
    clears, this code server would keep running unchecked -- the exact skew
    the contract exists to reject.
    """
    # No explicit OPENLAKEFORGE_FLOE_DAGSTER_MANIFEST_SOURCE/ACCESS_MODE override:
    # remote is the resolved default already (_floe_manifest_access_mode()), so
    # the remote code path is exercised without also tripping the separate,
    # pre-existing "user explicitly demanded remote" no-fallback check, which
    # is unrelated to this fix and would make this test pass for the wrong
    # reason.
    monkeypatch.delenv("OPENLAKEFORGE_FLOE_DAGSTER_MANIFEST_SOURCE", raising=False)
    monkeypatch.delenv("OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE", raising=False)
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_REVISION", "sha256:expected")
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI", "s3://ops/floe/manifests")
    import libs.product_dagster as pd

    def _raise_transient(uri: str) -> str:
        raise ConnectionError("simulated transient S3 read failure")

    monkeypatch.setattr(pd, "read_text_uri", _raise_transient)
    spec = _spec_with_local_manifest(tmp_path)
    assert spec.manifest_path.exists()  # the fallback file genuinely exists

    with pytest.raises(RuntimeError, match="Failed to load remote Floe manifest"):
        pd._manifest_path_for_dagster(spec)


def test_transient_fetch_failure_falls_back_to_local_when_revision_is_manual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing debug/local-fallback behavior must be preserved when
    revision enforcement is disabled (the default).
    """
    monkeypatch.delenv("OPENLAKEFORGE_FLOE_DAGSTER_MANIFEST_SOURCE", raising=False)
    monkeypatch.delenv("OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE", raising=False)
    monkeypatch.delenv("OPENLAKEFORGE_FLOE_MANIFEST_REVISION", raising=False)
    monkeypatch.setenv("OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI", "s3://ops/floe/manifests")
    import libs.product_dagster as pd

    def _raise_transient(uri: str) -> str:
        raise ConnectionError("simulated transient S3 read failure")

    monkeypatch.setattr(pd, "read_text_uri", _raise_transient)
    spec = _spec_with_local_manifest(tmp_path)

    result = pd._manifest_path_for_dagster(spec)
    assert result == str(spec.manifest_path)
