import hashlib
from pathlib import Path

import pytest
from test_revision import FakeS3Client
from typer.testing import CliRunner

import olf
from olf import k8s as k8s_module
from olf import revision as revision_module
from olf import s3 as s3_module
from olf.cli import app

runner = CliRunner()


def test_version_command_prints_package_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == olf.__version__


def test_upload_manifests_also_publishes_a_matching_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`olf artifacts upload-manifests` must be self-consistent when invoked
    standalone, as `make floe-manifest-upload` advertises: the per-object
    revision metadata it stamps and the floe/manifests/REVISION sidecar
    `revision verify` reads must always agree, not only when the caller also
    happens to run `olf revision publish` afterward as part of a full deploy.
    """
    domain_dir = tmp_path / "domains/sales/contracts/floe/manifests"
    domain_dir.mkdir(parents=True)
    (domain_dir / "order_revenue.manifest.json").write_text('{"placeholder": true}')

    monkeypatch.setenv("OPENLAKEFORGE_OPS_BUCKET_NAME", "ops-bucket")
    monkeypatch.setenv("OPENLAKEFORGE_KUBE_NAMESPACE", "lakehouse")
    monkeypatch.setenv("OPENLAKEFORGE_REPO_ROOT", str(tmp_path))

    monkeypatch.setattr(k8s_module, "secret_value", lambda *args, **kwargs: "dummy")

    fake_client = FakeS3Client()

    class _FakePortForwardClient:
        def __enter__(self):
            return fake_client

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(s3_module, "port_forward_client", lambda *args, **kwargs: _FakePortForwardClient())

    result = runner.invoke(app, ["artifacts", "upload-manifests", "--via", "port-forward"])

    assert result.exit_code == 0, result.output
    # The uploaded manifest and the sidecar must describe the same revision.
    uploaded_manifest_key = "floe/manifests/sales/order_revenue/order_revenue.manifest.json"
    assert uploaded_manifest_key in fake_client.objects
    sidecar = revision_module.fetch_sidecar(fake_client, "ops-bucket")

    expected_hash = hashlib.sha256((domain_dir / "order_revenue.manifest.json").read_bytes()).hexdigest()
    expected_revision = revision_module.manifest_from_entries({uploaded_manifest_key: expected_hash})

    assert sidecar.entries[uploaded_manifest_key] == expected_hash
    assert sidecar.revision == expected_revision.revision
    # And the per-object metadata the upload stamped must match the same aggregate.
    assert fake_client.metadata[uploaded_manifest_key][revision_module.REVISION_METADATA_KEY] == sidecar.revision
