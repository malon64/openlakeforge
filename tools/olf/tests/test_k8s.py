import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from olf import k8s


def test_kubectl_passes_kubeconfig_path_via_subprocess_env(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        return FakeCompleted()

    monkeypatch.setattr(k8s.subprocess, "run", fake_run)
    monkeypatch.setenv("KUBE_CONTEXT", "kind-openlakeforge-local")

    k8s._kubectl(["get", "pods"], kubeconfig_path="/custom/kubeconfig")

    assert calls[0]["env"]["KUBECONFIG"] == "/custom/kubeconfig"


def test_kubectl_leaves_env_untouched_without_kubeconfig_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        return FakeCompleted()

    monkeypatch.setattr(k8s.subprocess, "run", fake_run)
    monkeypatch.setenv("KUBE_CONTEXT", "kind-openlakeforge-local")

    k8s._kubectl(["get", "pods"])

    assert calls[0]["env"] is None


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
    monkeypatch.setattr(k8s, "_wait_for_port_forward", Mock())

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


def test_wait_for_port_forward_retries_until_the_listener_accepts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = Mock()
    process.poll.return_value = None
    attempts = iter((OSError("not ready"), MagicMock()))

    def create_connection(*_args: object, **_kwargs: object) -> MagicMock:
        result = next(attempts)
        if isinstance(result, OSError):
            raise result
        return result

    monkeypatch.setattr(k8s.socket, "create_connection", create_connection)
    monkeypatch.setattr(k8s.time, "sleep", lambda _delay: None)

    k8s._wait_for_port_forward(process, 18088, str(tmp_path / "port-forward.log"))


def test_wait_for_port_forward_reports_an_early_process_exit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log_path = tmp_path / "port-forward.log"
    log_path.write_text("error: services \"missing\" not found\n")
    process = Mock()
    process.poll.return_value = 1

    with pytest.raises(k8s.KubectlError, match="services .*missing.*not found"):
        k8s._wait_for_port_forward(process, 18088, str(log_path))


def test_wait_for_port_forward_times_out_and_reports_the_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log_path = tmp_path / "port-forward.log"
    log_path.write_text("still starting\n")
    process = Mock()
    process.poll.return_value = None
    clock = iter((0.0, 0.0, k8s.PORT_FORWARD_READY_TIMEOUT_SECONDS + 0.1))

    monkeypatch.setattr(k8s.socket, "create_connection", Mock(side_effect=OSError("not ready")))
    monkeypatch.setattr(k8s.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(k8s.time, "sleep", lambda _delay: None)

    with pytest.raises(k8s.KubectlError, match=r"(?s)timed out.*still starting"):
        k8s._wait_for_port_forward(process, 18088, str(log_path))


def test_port_forward_cleans_up_after_readiness_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    process = Mock()
    process.poll.return_value = 0
    monkeypatch.setattr(k8s.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(k8s, "_wait_for_port_forward", Mock(side_effect=k8s.KubectlError("not ready")))

    with pytest.raises(k8s.KubectlError, match="not ready"):
        with k8s.port_forward(
            "polaris",
            8181,
            "lakehouse",
            log_path=str(tmp_path / "port-forward.log"),
            kube_context="kind-openlakeforge-local",
        ):
            pass

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
    calls: list[list[str]] = []
    monkeypatch.setattr(
        k8s,
        "_kubectl",
        lambda args, **kwargs: calls.append(args)
        or "\n".join(
            [
                "dagster-user-deployments-openlakeforge-dagster",
                "dagster-dagster-user-deployments-current-chart",
                "dagster-user-deployments-domain-a",
                "dagster-user-deployments-domain-b",
            ]
        ),
    )

    deployments = k8s.discover_dagster_user_deployments("lakehouse")

    assert deployments == [
        "dagster-user-deployments-openlakeforge-dagster",
        "dagster-dagster-user-deployments-current-chart",
        "dagster-user-deployments-domain-a",
        "dagster-user-deployments-domain-b",
    ]
    assert calls == [
        [
            "get",
            "deployments",
            "-n",
            "lakehouse",
            "-l",
            "app.kubernetes.io/name=dagster-user-deployments,app.kubernetes.io/instance=dagster",
            "-o",
            'jsonpath={range .items[*]}{.metadata.name}{"\\n"}{end}',
        ]
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
        lambda namespace: ["dagster-user-deployments-domain-a", "dagster-user-deployments-domain-b"],
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
        ("dagster-user-deployments-domain-a", "repo/project-code:new", "lakehouse"),
        ("dagster-user-deployments-domain-b", "repo/project-code:new", "lakehouse"),
    ]
    assert len({restarted_at for *_, restarted_at in deployment_images}) == 1
    assert cronjob_images == [("openlakeforge-k8s-log-archive", "repo/project-code:new", "lakehouse")]
    assert rollout_waits == [(deployment, "lakehouse", "600s") for deployment, *_ in deployment_images]


def test_discover_superset_deployments_selects_by_chart_ownership_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirmed empirically against the pinned chart (`helm template
    superset/superset --version 0.15.5`): it labels every Deployment it owns
    with the pre-app.kubernetes.io/* Helm convention (release=<release-name>,
    heritage=Helm), not app.kubernetes.io/instance. Filtering server-side via
    that label selector -- not a client-side name-prefix match -- is what
    makes this safe once a namespace can be adopted from outside this tool
    (--adopt-namespace): a foreign deployment named e.g. "superset-exporter"
    would match a name prefix but never carry the chart's own labels.
    """
    calls: list[list[str]] = []

    def fake_kubectl(args, **kwargs):
        calls.append(args)
        return "\n".join(["superset", "superset-worker"])

    monkeypatch.setattr(k8s, "_kubectl", fake_kubectl)

    deployments = k8s.discover_superset_deployments("lakehouse")

    assert deployments == ["superset", "superset-worker"]
    assert calls == [
        [
            "get",
            "deployments",
            "-n",
            "lakehouse",
            "-l",
            "release=superset,heritage=Helm",
            "-o",
            'jsonpath={range .items[*]}{.metadata.name}{"\\n"}{end}',
        ]
    ]


def test_discover_superset_deployments_uses_the_given_release_name(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(k8s, "_kubectl", lambda args, **kwargs: calls.append(args) or "")

    k8s.discover_superset_deployments("lakehouse", release_name="custom-superset")

    assert "-l" in calls[0]
    assert calls[0][calls[0].index("-l") + 1] == "release=custom-superset,heritage=Helm"


def test_set_superset_image_patches_discovered_deployments_and_waits_for_rollout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(k8s, "discover_superset_deployments", lambda namespace: ["superset", "superset-worker"])
    patch_calls = []
    monkeypatch.setattr(
        k8s,
        "patch_deployment_image_if_exists",
        lambda deployment, image, namespace, **kwargs: (
            patch_calls.append((deployment, image, namespace, kwargs["restarted_at"])) or True
        ),
    )
    rollout_waits = []
    monkeypatch.setattr(
        k8s,
        "_wait_for_rollout_with_diagnostics",
        lambda deployment, namespace, timeout: rollout_waits.append((deployment, namespace, timeout)),
    )

    k8s.set_superset_image("repo/superset@sha256:" + "a" * 64, "lakehouse")

    assert [(d, i, n) for d, i, n, _ in patch_calls] == [
        ("superset", "repo/superset@sha256:" + "a" * 64, "lakehouse"),
        ("superset-worker", "repo/superset@sha256:" + "a" * 64, "lakehouse"),
    ]
    assert len({restarted_at for *_, restarted_at in patch_calls}) == 1
    assert rollout_waits == [("superset", "lakehouse", "600s"), ("superset-worker", "lakehouse", "600s")]


def test_set_superset_image_skips_rollout_wait_when_deployment_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(k8s, "discover_superset_deployments", lambda namespace: ["superset"])
    monkeypatch.setattr(k8s, "patch_deployment_image_if_exists", lambda *a, **k: False)
    rollout_waits = []
    monkeypatch.setattr(
        k8s, "_wait_for_rollout_with_diagnostics", lambda *a: rollout_waits.append(a)
    )

    k8s.set_superset_image("repo/superset@sha256:" + "a" * 64, "lakehouse")

    assert rollout_waits == []


def test_pod_images_from_payload_extracts_containers_but_not_init_containers() -> None:
    """initContainers are excluded entirely (see the function's docstring for
    the concrete Dagster check-db-ready/postgres collision that motivated
    this): they are ephemeral setup steps, not "what's running", and their
    images are unrelated to anything a release manifest declares.
    """
    payload = {
        "items": [
            {
                "metadata": {"name": "trino-0"},
                "spec": {
                    "containers": [{"name": "trino", "image": "trinodb/trino:480"}],
                    "initContainers": [{"name": "wait-for-catalog", "image": "busybox:1.36"}],
                },
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {
                            "name": "trino",
                            "image": "trinodb/trino:480",
                            "imageID": "docker-pullable://trinodb/trino@sha256:" + "e" * 64,
                        }
                    ],
                    "initContainerStatuses": [
                        {
                            "name": "wait-for-catalog",
                            "image": "busybox:1.36",
                            "imageID": "busybox@sha256:" + "f" * 64,
                        }
                    ],
                },
            },
            {
                "metadata": {"name": "pending-pod"},
                "spec": {"containers": [{"name": "app", "image": "repo/app:latest"}]},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"name": "app", "image": "repo/app:latest", "imageID": ""}],
                },
            },
        ]
    }

    entries = k8s.pod_images_from_payload(payload)

    assert entries == [
        {
            "pod": "trino-0",
            "container": "trino",
            "image": "trinodb/trino:480",
            "image_id": "trinodb/trino@sha256:" + "e" * 64,
        },
        {"pod": "pending-pod", "container": "app", "image": "repo/app:latest", "image_id": ""},
    ]


def test_pod_images_from_payload_handles_empty_items() -> None:
    assert k8s.pod_images_from_payload({}) == []
    assert k8s.pod_images_from_payload({"items": []}) == []


def test_pod_images_from_payload_excludes_completed_and_pending_pods() -> None:
    """A completed bootstrap Job pod (e.g. Polaris's) must not surface its
    image -- it is deliberately unregistered in expected_images, so
    including it would report it as drift on an otherwise healthy install.
    """
    payload = {
        "items": [
            {
                "metadata": {"name": "polaris-bootstrap-abcdef"},
                "spec": {"containers": [{"name": "bootstrap", "image": "alpine/k8s:1.30.0"}]},
                "status": {
                    "phase": "Succeeded",
                    "containerStatuses": [
                        {
                            "name": "bootstrap",
                            "image": "alpine/k8s:1.30.0",
                            "imageID": "alpine/k8s@sha256:" + "a" * 64,
                        }
                    ],
                },
            },
            {
                "metadata": {"name": "still-scheduling"},
                "spec": {"containers": [{"name": "app", "image": "repo/app:latest"}]},
                "status": {"phase": "Pending"},
            },
        ]
    }

    assert k8s.pod_images_from_payload(payload) == []


def test_pod_images_from_payload_ignores_dagster_readiness_wait_init_containers() -> None:
    """Regression test for a real CI failure: the Dagster chart's default
    check-db-ready init container runs a DIFFERENT postgres version
    (postgres:14.6, a pg_isready probe) than the actual deployed PostgreSQL
    (postgres:16-alpine). Both canonicalize to the same
    docker.io/library/postgres repository, so before initContainers were
    excluded, this produced a false MISMATCH (two digests for one expected
    image) on an entirely healthy install.
    """
    payload = {
        "items": [
            {
                "metadata": {"name": "dagster-daemon-abc"},
                "spec": {
                    "initContainers": [
                        {"name": "check-db-ready", "image": "docker.io/library/postgres:14.6"},
                        {"name": "init-user-deployment", "image": "docker.io/busybox:1.28"},
                    ],
                    "containers": [
                        {"name": "dagster-daemon", "image": "ghcr.io/malon64/openlakeforge/project-code:0.1.0-alpha.1"}
                    ],
                },
                "status": {
                    "phase": "Running",
                    "initContainerStatuses": [
                        {
                            "name": "check-db-ready",
                            "image": "docker.io/library/postgres:14.6",
                            "imageID": "docker.io/library/postgres@sha256:" + "9" * 64,
                        },
                        {
                            "name": "init-user-deployment",
                            "image": "docker.io/busybox:1.28",
                            "imageID": "docker.io/library/busybox@sha256:" + "8" * 64,
                        },
                    ],
                    "containerStatuses": [
                        {
                            "name": "dagster-daemon",
                            "image": "ghcr.io/malon64/openlakeforge/project-code:0.1.0-alpha.1",
                            "imageID": "ghcr.io/malon64/openlakeforge/project-code@sha256:" + "4" * 64,
                        }
                    ],
                },
            }
        ]
    }

    entries = k8s.pod_images_from_payload(payload)

    assert [entry["container"] for entry in entries] == ["dagster-daemon"]


def test_list_pod_images_parses_kubectl_json(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict]] = []
    payload = {
        "items": [
            {
                "metadata": {"name": "polaris-0"},
                "spec": {"containers": [{"name": "polaris", "image": "apache/polaris:1.4.0"}]},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {
                            "name": "polaris",
                            "image": "apache/polaris:1.4.0",
                            "imageID": "apache/polaris@sha256:" + "a" * 64,
                        }
                    ],
                },
            }
        ]
    }
    monkeypatch.setattr(
        k8s, "_kubectl", lambda args, **kwargs: calls.append((args, kwargs)) or json.dumps(payload)
    )

    entries = k8s.list_pod_images("lakehouse", kube_context="kind-openlakeforge-local")

    assert calls == [
        (
            ["get", "pods", "-n", "lakehouse", "-o", "json"],
            {"capture": True, "kube_context": "kind-openlakeforge-local", "kubeconfig_path": None},
        )
    ]
    assert entries == [
        {
            "pod": "polaris-0",
            "container": "polaris",
            "image": "apache/polaris:1.4.0",
            "image_id": "apache/polaris@sha256:" + "a" * 64,
        }
    ]
