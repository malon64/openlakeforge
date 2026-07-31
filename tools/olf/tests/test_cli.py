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


def test_upload_manifests_without_runtime_root_does_not_prune_configs_or_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`make floe-manifest-upload` (this command with no --runtime-root) uploads
    manifests only, via discover_tracked_manifests. Its sidecar publish must not
    reconcile floe/configs/ or floe/profiles/ against that manifest-only entry
    set -- doing so would delete every config and profile a prior full deploy
    uploaded, since none of their keys are declared here. Only a stale manifest
    (one this invocation's tracked set no longer declares) should be pruned.
    """
    domain_dir = tmp_path / "domains/sales/contracts/floe/manifests"
    domain_dir.mkdir(parents=True)
    (domain_dir / "order_revenue.manifest.json").write_text('{"placeholder": true}')

    monkeypatch.setenv("OPENLAKEFORGE_OPS_BUCKET_NAME", "ops-bucket")
    monkeypatch.setenv("OPENLAKEFORGE_KUBE_NAMESPACE", "lakehouse")
    monkeypatch.setenv("OPENLAKEFORGE_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(k8s_module, "secret_value", lambda *args, **kwargs: "dummy")

    fake_client = FakeS3Client()
    # Simulate objects a prior full deploy (olf revision publish --runtime-root)
    # already uploaded, which this manifest-only invocation has no opinion on.
    fake_client.put_object(
        Bucket="ops-bucket",
        Key="floe/configs/sales/order_revenue/order_revenue.yml",
        Body=b"config",
        ContentType="application/json",
    )
    fake_client.put_object(
        Bucket="ops-bucket",
        Key="floe/profiles/sales/order_revenue/local-k8s.yml",
        Body=b"profile",
        ContentType="application/json",
    )
    # And a manifest for a product removed since the last full deploy -- this
    # one SHOULD still be pruned, since floe/manifests/ IS represented in the
    # current tracked set.
    fake_client.put_object(
        Bucket="ops-bucket",
        Key="floe/manifests/sales/customer_health/customer_health.manifest.json",
        Body=b"stale-removed-product",
        ContentType="application/json",
    )

    class _FakePortForwardClient:
        def __enter__(self):
            return fake_client

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(s3_module, "port_forward_client", lambda *args, **kwargs: _FakePortForwardClient())

    result = runner.invoke(app, ["artifacts", "upload-manifests", "--via", "port-forward"])

    assert result.exit_code == 0, result.output
    assert "floe/configs/sales/order_revenue/order_revenue.yml" in fake_client.objects
    assert "floe/profiles/sales/order_revenue/local-k8s.yml" in fake_client.objects
    assert "floe/manifests/sales/customer_health/customer_health.manifest.json" not in fake_client.objects
    assert "floe/manifests/sales/order_revenue/order_revenue.manifest.json" in fake_client.objects

    # The published sidecar must describe the bucket's *true total* state --
    # including the configs/profiles this invocation preserved but never had
    # local knowledge of -- not just the manifest-only set it uploaded.
    # Otherwise `revision verify` would see every preserved config/profile as
    # an undeclared extra forever, since this command never touches them again.
    sidecar = revision_module.fetch_sidecar(fake_client, "ops-bucket")
    assert "floe/configs/sales/order_revenue/order_revenue.yml" in sidecar.entries
    assert "floe/profiles/sales/order_revenue/local-k8s.yml" in sidecar.entries
    assert "floe/manifests/sales/order_revenue/order_revenue.manifest.json" in sidecar.entries
    assert "floe/manifests/sales/customer_health/customer_health.manifest.json" not in sidecar.entries

    actual_full_bucket = revision_module.fetch_bucket_manifest(fake_client, "ops-bucket")
    assert sidecar.revision == actual_full_bucket.revision


def _upload_manifests_with_live_cluster_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    current_revision: str | None,
):
    """Shared setup for the tests below: run `upload-manifests` against a fake
    bucket while monkeypatching k8s_module to report a given already-deployed
    revision, and return (calls, result) where `calls` records any
    set_project_code_image invocation (must always stay empty -- see the
    tests) and `result` is the CliRunner invocation, for asserting on the
    warning text.
    """
    domain_dir = tmp_path / "domains/sales/contracts/floe/manifests"
    domain_dir.mkdir(parents=True)
    (domain_dir / "order_revenue.manifest.json").write_text('{"placeholder": true}')

    monkeypatch.setenv("OPENLAKEFORGE_OPS_BUCKET_NAME", "ops-bucket")
    monkeypatch.setenv("OPENLAKEFORGE_KUBE_NAMESPACE", "lakehouse")
    monkeypatch.setenv("OPENLAKEFORGE_REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(k8s_module, "secret_value", lambda *args, **kwargs: "dummy")
    monkeypatch.setattr(k8s_module, "current_floe_manifest_revision", lambda namespace: current_revision)

    calls = []
    monkeypatch.setattr(
        k8s_module,
        "set_project_code_image",
        lambda image, namespace, **kwargs: calls.append((image, namespace, kwargs)),
    )

    fake_client = FakeS3Client()

    class _FakePortForwardClient:
        def __enter__(self):
            return fake_client

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(s3_module, "port_forward_client", lambda *args, **kwargs: _FakePortForwardClient())

    result = runner.invoke(app, ["artifacts", "upload-manifests", "--via", "port-forward"])
    assert result.exit_code == 0, result.output
    return calls, result


def test_upload_manifests_warns_but_does_not_roll_when_revision_contract_is_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the artifact revision contract is already active on the running
    deployment, a standalone upload that advances the bucket to a new
    revision must NOT roll Dagster onto it: the running image's
    OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT is baked in at image build
    time, so patching only the OPENLAKEFORGE_FLOE_MANIFEST_REVISION
    expectation onto the *same* image would make _validate_artifact_revision
    reject every new pod at startup -- a self-inflicted outage. It must warn
    that a full (image-rebuilding) deploy is required instead.
    """
    calls, result = _upload_manifests_with_live_cluster_state(
        tmp_path, monkeypatch, current_revision="sha256:stale-old"
    )

    assert calls == []
    assert "Not rolling Dagster" in result.output
    assert "full artifact deploy" in result.output


def test_upload_manifests_does_not_roll_dagster_when_revision_is_manual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing debug/local-only workflow (no revision tracking deployed
    yet) must not suddenly start forcing rollouts from a standalone upload.
    """
    calls, _result = _upload_manifests_with_live_cluster_state(tmp_path, monkeypatch, current_revision="manual")
    assert calls == []


def test_upload_manifests_does_not_roll_dagster_when_no_deployment_exists_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls, _result = _upload_manifests_with_live_cluster_state(tmp_path, monkeypatch, current_revision=None)
    assert calls == []
