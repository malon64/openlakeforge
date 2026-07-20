"""Kubernetes helpers that shell out to kubectl.

Shell scripts stay the orchestrators for terraform/kubectl/helm/docker, but the
image-bookkeeping and port-forward logic used to be copy-pasted (as Python
heredocs) across the Azure and AWS artifact deploy scripts. This module is the
single implementation. The JSON-shaping helpers are kept pure so they can be
unit tested without a cluster.
"""

from __future__ import annotations

import base64
import contextlib
import json
import socket
import subprocess
import time
from collections.abc import Iterator

from olf import log


class KubectlError(RuntimeError):
    pass


def _kubectl(args: list[str], *, capture: bool = False, check: bool = True) -> str:
    result = subprocess.run(
        ["kubectl", *args],
        capture_output=capture,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or "").strip()
        raise KubectlError(f"kubectl {' '.join(args)} failed: {detail}")
    return result.stdout if capture else ""


def resource_exists(kind: str, name: str, namespace: str) -> bool:
    result = subprocess.run(
        ["kubectl", "get", kind, name, "-n", namespace],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def secret_value(
    secret_name: str,
    key: str,
    namespace: str,
    *,
    kube_context: str | None = None,
) -> str:
    args = []
    if kube_context:
        args.extend(["--context", kube_context])
    args.extend(
        ["get", "secret", secret_name, "-n", namespace, "-o", f"jsonpath={{.data.{key}}}"]
    )
    raw = _kubectl(
        args,
        capture=True,
    )
    return base64.b64decode(raw).decode("utf-8")


def _free_local_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def port_forward(
    service: str,
    remote_port: int,
    namespace: str,
    *,
    local_port: int | None = None,
    log_path: str = "/tmp/openlakeforge-port-forward.log",
    kube_context: str | None = None,
) -> Iterator[int]:
    """Run `kubectl port-forward` for the block, yielding the local port.

    Callers own readiness polling because each service has a different health
    probe (Polaris OAuth, OpenMetadata JWKS, S3 head-bucket).
    """
    port = local_port or _free_local_port()
    command = ["kubectl"]
    if kube_context:
        command.extend(["--context", kube_context])
    command.extend(
        [
            "port-forward",
            f"svc/{service}",
            f"{port}:{remote_port}",
            "-n",
            namespace,
        ]
    )
    with open(log_path, "w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    try:
        yield port
    finally:
        process.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=10)
        if process.poll() is None:
            process.kill()


def discover_dagster_user_deployments(namespace: str) -> list[str]:
    """Domain Dagster code locations are chart-generated; discover them."""
    raw = _kubectl(
        [
            "get",
            "deployments",
            "-n",
            namespace,
            "-o",
            'jsonpath={range .items[*]}{.metadata.name}{"\\n"}{end}',
        ],
        capture=True,
    )
    return [
        name
        for name in raw.splitlines()
        if name.startswith("dagster-user-deployments-") and name.endswith("-dagster")
    ]


# --- Pure image-patch builders (unit tested) -------------------------------


def dagster_yaml_with_job_image(dagster_yaml: str, image: str) -> str:
    """Rewrite the run launcher job_image line in a dagster.yaml document."""
    lines = dagster_yaml.splitlines()
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("job_image:"):
            indent = line[: len(line) - len(stripped)]
            lines[index] = f'{indent}job_image: "{image}"'
            break
    else:
        raise KubectlError("dagster-instance ConfigMap does not contain run launcher job_image.")
    return "\n".join(lines) + ("\n" if dagster_yaml.endswith("\n") else "")


def deployment_container_patch(containers: list[dict], image: str) -> dict:
    """Build a strategic-merge patch that bumps regular container images.

    Keeps DAGSTER_CURRENT_IMAGE in sync where already set: Dagster's
    K8sRunLauncher derives run-pod images from the code location's
    DAGSTER_CURRENT_IMAGE, which overrides run_launcher.job_image. Bumping only
    the container image leaves run pods launching on the previous image.
    """
    if not containers:
        raise KubectlError("deployment has no regular containers to patch.")

    def one(container: dict) -> dict:
        entry = {"name": container["name"], "image": image}
        env = container.get("env") or []
        if any(var.get("name") == "DAGSTER_CURRENT_IMAGE" for var in env):
            entry["env"] = [{"name": "DAGSTER_CURRENT_IMAGE", "value": image}]
        return entry

    return {"spec": {"template": {"spec": {"containers": [one(c) for c in containers]}}}}


def cronjob_container_patch(containers: list[dict], image: str) -> dict:
    if not containers:
        raise KubectlError("cronjob has no regular containers to patch.")
    return {
        "spec": {
            "jobTemplate": {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {"name": c["name"], "image": image} for c in containers
                            ]
                        }
                    }
                }
            }
        }
    }


# --- Cluster-mutating image operations -------------------------------------


def _get_json(kind: str, name: str, namespace: str) -> dict:
    raw = _kubectl(["get", kind, name, "-n", namespace, "-o", "json"], capture=True)
    return json.loads(raw)


def patch_dagster_instance_configmap(image: str, namespace: str) -> None:
    configmap = "dagster-instance"
    if not resource_exists("configmap", configmap, namespace):
        return
    log.step(f"Updating Dagster run launcher image to {image}...")
    payload = _get_json("configmap", configmap, namespace)
    dagster_yaml = (payload.get("data") or {}).get("dagster.yaml")
    if dagster_yaml is None:
        return
    updated = dagster_yaml_with_job_image(dagster_yaml, image)
    patch = json.dumps({"data": {"dagster.yaml": updated}})
    _kubectl(["patch", "configmap", configmap, "-n", namespace, "--type", "merge", "-p", patch])


def patch_deployment_image_if_exists(deployment: str, image: str, namespace: str) -> None:
    if not resource_exists("deployment", deployment, namespace):
        return
    log.step(f"Updating {deployment} image to {image}...")
    payload = _get_json("deployment", deployment, namespace)
    containers = payload["spec"]["template"]["spec"].get("containers", [])
    patch = deployment_container_patch(containers, image)
    _kubectl(
        ["patch", "deployment", deployment, "-n", namespace, "--type", "strategic", "-p", json.dumps(patch)]
    )


def patch_cronjob_image_if_exists(cronjob: str, image: str, namespace: str) -> None:
    if not resource_exists("cronjob", cronjob, namespace):
        return
    log.step(f"Updating {cronjob} image to {image}...")
    payload = _get_json("cronjob", cronjob, namespace)
    containers = payload["spec"]["jobTemplate"]["spec"]["template"]["spec"].get("containers", [])
    patch = cronjob_container_patch(containers, image)
    _kubectl(
        ["patch", "cronjob", cronjob, "-n", namespace, "--type", "strategic", "-p", json.dumps(patch)]
    )


_DAGSTER_CORE_DEPLOYMENTS = (
    "dagster-dagster-webserver",
    "dagster-dagster-daemon",
    "dagster-webserver",
    "dagster-daemon",
)


def set_project_code_image(image: str, namespace: str) -> None:
    """Point every Dagster surface at the freshly pushed project-code image."""
    patch_dagster_instance_configmap(image, namespace)
    for deployment in _DAGSTER_CORE_DEPLOYMENTS:
        patch_deployment_image_if_exists(deployment, image, namespace)
    patch_cronjob_image_if_exists("openlakeforge-k8s-log-archive", image, namespace)
    for deployment in discover_dagster_user_deployments(namespace):
        patch_deployment_image_if_exists(deployment, image, namespace)


def restart_deployment_if_exists(deployment: str, namespace: str, timeout: str = "600s") -> None:
    if not resource_exists("deployment", deployment, namespace):
        return
    log.step(f"Restarting {deployment}...")
    _kubectl(["rollout", "restart", f"deployment/{deployment}", "-n", namespace])
    _kubectl(["rollout", "status", f"deployment/{deployment}", "-n", namespace, f"--timeout={timeout}"])


def restart_dagster_project_code_deployments(namespace: str) -> None:
    for deployment in _DAGSTER_CORE_DEPLOYMENTS:
        restart_deployment_if_exists(deployment, namespace)
    for deployment in discover_dagster_user_deployments(namespace):
        restart_deployment_if_exists(deployment, namespace)


def wait_for_rollout(kind_name: str, namespace: str, timeout: str = "300s") -> None:
    _kubectl(["rollout", "status", kind_name, "-n", namespace, f"--timeout={timeout}"])


def http_wait(url: str, *, attempts: int = 60, delay: float = 2.0) -> bool:
    """Poll an HTTP endpoint until it answers 2xx/3xx/4xx (i.e. reachable)."""
    import urllib.error
    import urllib.request

    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2):  # noqa: S310 - localhost port-forward
                return True
        except urllib.error.HTTPError:
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(delay)
    return False
