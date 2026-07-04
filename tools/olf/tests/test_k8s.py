import pytest

from olf import k8s


def test_dagster_yaml_job_image_rewrite_preserves_indent_and_trailing_newline() -> None:
    src = "run_launcher:\n  config:\n    job_image: \"old:tag\"\n"
    out = k8s.dagster_yaml_with_job_image(src, "new:tag")
    assert '    job_image: "new:tag"' in out
    assert out.endswith("\n")


def test_dagster_yaml_job_image_missing_raises() -> None:
    with pytest.raises(k8s.KubectlError):
        k8s.dagster_yaml_with_job_image("run_launcher:\n  config: {}\n", "new:tag")


def test_deployment_patch_syncs_dagster_current_image() -> None:
    containers = [
        {"name": "dagster", "env": [{"name": "DAGSTER_CURRENT_IMAGE", "value": "old"}]},
        {"name": "sidecar"},
    ]
    patch = k8s.deployment_container_patch(containers, "new:tag")
    entries = patch["spec"]["template"]["spec"]["containers"]
    dagster_entry = next(c for c in entries if c["name"] == "dagster")
    sidecar_entry = next(c for c in entries if c["name"] == "sidecar")
    assert dagster_entry["image"] == "new:tag"
    assert dagster_entry["env"] == [{"name": "DAGSTER_CURRENT_IMAGE", "value": "new:tag"}]
    assert sidecar_entry == {"name": "sidecar", "image": "new:tag"}


def test_deployment_patch_without_containers_raises() -> None:
    with pytest.raises(k8s.KubectlError):
        k8s.deployment_container_patch([], "new:tag")


def test_cronjob_patch_shape() -> None:
    patch = k8s.cronjob_container_patch([{"name": "archive"}], "new:tag")
    containers = patch["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    assert containers == [{"name": "archive", "image": "new:tag"}]
