import base64
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from olf import k8s


def test_secret_value_uses_explicit_kube_context(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict]] = []
    encoded = base64.b64encode(b"secret").decode("ascii")
    monkeypatch.setattr(
        k8s,
        "_kubectl",
        lambda args, **kwargs: calls.append((args, kwargs)) or encoded,
    )

    value = k8s.secret_value(
        "seaweedfs-s3-creds",
        "AWS_ACCESS_KEY_ID",
        "lakehouse",
        kube_context="kind-openlakeforge-local",
    )

    assert value == "secret"
    assert calls == [
        (
            [
                "get",
                "secret",
                "seaweedfs-s3-creds",
                "-n",
                "lakehouse",
                "-o",
                "jsonpath={.data.AWS_ACCESS_KEY_ID}",
            ],
            {"capture": True, "kube_context": "kind-openlakeforge-local"},
        )
    ]


def test_kubectl_command_uses_environment_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KUBE_CONTEXT", "aks-openlakeforge-poc")

    assert k8s.kubectl_command(["get", "pods"]) == [
        "kubectl",
        "--context",
        "aks-openlakeforge-poc",
        "get",
        "pods",
    ]


def test_kubectl_command_requires_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KUBE_CONTEXT", raising=False)

    with pytest.raises(k8s.KubectlError, match="KUBE_CONTEXT is required"):
        k8s.kubectl_command(["get", "pods"])


def test_port_forward_uses_explicit_kube_context(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    process = Mock()
    process.poll.return_value = 0
    popen = Mock(return_value=process)
    monkeypatch.setattr(k8s.subprocess, "Popen", popen)

    with k8s.port_forward(
        "superset",
        8088,
        "lakehouse",
        local_port=18088,
        log_path=str(tmp_path / "port-forward.log"),
        kube_context="kind-openlakeforge-local",
    ) as local_port:
        assert local_port == 18088

    assert popen.call_args.args[0] == [
        "kubectl",
        "--context",
        "kind-openlakeforge-local",
        "port-forward",
        "svc/superset",
        "18088:8088",
        "-n",
        "lakehouse",
    ]
    process.terminate.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=10)


def test_dagster_yaml_job_image_rewrite_preserves_indent_and_trailing_newline() -> None:
    src = 'run_launcher:\n  config:\n    job_image: "old:tag"\n'
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


def test_deployment_patch_combines_image_and_rollout_annotation() -> None:
    patch = k8s.deployment_container_patch(
        [{"name": "dagster"}],
        "new:tag",
        restarted_at="2026-07-20T12:00:00+00:00",
    )

    assert patch["spec"]["template"]["metadata"]["annotations"] == {
        "kubectl.kubernetes.io/restartedAt": "2026-07-20T12:00:00+00:00"
    }
    assert patch["spec"]["template"]["spec"]["containers"] == [{"name": "dagster", "image": "new:tag"}]


def test_deployment_patch_without_containers_raises() -> None:
    with pytest.raises(k8s.KubectlError):
        k8s.deployment_container_patch([], "new:tag")


def test_cronjob_patch_shape() -> None:
    patch = k8s.cronjob_container_patch([{"name": "archive"}], "new:tag")
    containers = patch["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
    assert containers == [{"name": "archive", "image": "new:tag"}]


def test_patch_dagster_instance_configmap_rewrites_dagster_yaml(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    dagster_yaml = 'run_launcher:\n  config:\n    job_image: "repo/project-code:old"\n'

    monkeypatch.setattr(
        k8s,
        "resource_exists",
        lambda kind, name, namespace: (kind, name, namespace) == ("configmap", "dagster-instance", "lakehouse"),
    )
    monkeypatch.setattr(
        k8s,
        "_get_json",
        lambda kind, name, namespace: {"data": {"dagster.yaml": dagster_yaml}},
    )
    monkeypatch.setattr(k8s, "_kubectl", lambda args, **kwargs: calls.append(args) or "")

    k8s.patch_dagster_instance_configmap("repo/project-code:new", "lakehouse")

    assert calls == [
        [
            "patch",
            "configmap",
            "dagster-instance",
            "-n",
            "lakehouse",
            "--type",
            "merge",
            "-p",
            json.dumps(
                {"data": {"dagster.yaml": ('run_launcher:\n  config:\n    job_image: "repo/project-code:new"\n')}}
            ),
        ]
    ]


def test_patch_deployment_image_if_exists_uses_strategic_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(k8s, "resource_exists", lambda kind, name, namespace: True)
    monkeypatch.setattr(
        k8s,
        "_get_json",
        lambda kind, name, namespace: {"spec": {"template": {"spec": {"containers": [{"name": "dagster"}]}}}},
    )
    monkeypatch.setattr(k8s, "_kubectl", lambda args, **kwargs: calls.append(args) or "")

    k8s.patch_deployment_image_if_exists("dagster-webserver", "repo/project-code:new", "lakehouse")

    assert calls == [
        [
            "patch",
            "deployment",
            "dagster-webserver",
            "-n",
            "lakehouse",
            "--type",
            "strategic",
            "-p",
            json.dumps(
                {
                    "spec": {
                        "template": {"spec": {"containers": [{"name": "dagster", "image": "repo/project-code:new"}]}}
                    }
                }
            ),
        ]
    ]


def test_discover_dagster_user_deployments_filters_chart_generated_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        k8s,
        "_kubectl",
        lambda args, **kwargs: "\n".join(
            [
                "dagster-user-deployments-sales-dagster",
                "dagster-user-deployments-supply-chain-dagster",
                "dagster-webserver",
                "dagster-user-deployments-sales",
                "custom-dagster",
            ]
        ),
    )

    deployments = k8s.discover_dagster_user_deployments("lakehouse")

    assert deployments == [
        "dagster-user-deployments-sales-dagster",
        "dagster-user-deployments-supply-chain-dagster",
    ]


def test_set_project_code_image_updates_all_dagster_surfaces(monkeypatch: pytest.MonkeyPatch) -> None:
    configmap_images = []
    deployment_images = []
    cronjob_images = []
    monkeypatch.setattr(
        k8s,
        "patch_dagster_instance_configmap",
        lambda image, namespace: configmap_images.append(image),
    )
    monkeypatch.setattr(
        k8s,
        "patch_deployment_image_if_exists",
        lambda deployment, image, namespace, **kwargs: (
            deployment_images.append((deployment, image, namespace, kwargs["restarted_at"])) or True
        ),
    )
    monkeypatch.setattr(
        k8s,
        "patch_cronjob_image_if_exists",
        lambda cronjob, image, namespace: cronjob_images.append((cronjob, image, namespace)),
    )
    monkeypatch.setattr(
        k8s,
        "discover_dagster_user_deployments",
        lambda namespace: ["dagster-user-deployments-sales-dagster"],
    )
    rollout_waits = []
    monkeypatch.setattr(
        k8s,
        "_wait_for_rollout_with_diagnostics",
        lambda deployment, namespace, timeout: rollout_waits.append((deployment, namespace, timeout)),
    )

    k8s.set_project_code_image("repo/project-code:new", "lakehouse")

    assert configmap_images == ["repo/project-code:new"]
    assert [(deployment, image, namespace) for deployment, image, namespace, _ in deployment_images] == [
        ("dagster-dagster-webserver", "repo/project-code:new", "lakehouse"),
        ("dagster-dagster-daemon", "repo/project-code:new", "lakehouse"),
        ("dagster-webserver", "repo/project-code:new", "lakehouse"),
        ("dagster-daemon", "repo/project-code:new", "lakehouse"),
        ("dagster-user-deployments-sales-dagster", "repo/project-code:new", "lakehouse"),
    ]
    assert len({restarted_at for *_, restarted_at in deployment_images}) == 1
    assert cronjob_images == [("openlakeforge-k8s-log-archive", "repo/project-code:new", "lakehouse")]
    assert rollout_waits == [(deployment, "lakehouse", "600s") for deployment, *_ in deployment_images]
