"""Provider-neutral Kubernetes/Terraform reconciliation helpers.

Port of `scripts/lib/kube.sh`. Kept out of `olf.deployment.local` so #125 can
reuse it verbatim for AWS/Azure platform orchestration.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from olf import log
from olf.tooling.kubectl import Kubectl
from olf.tooling.terraform import Terraform


def namespace_exists(
    kubectl: Kubectl,
    namespace: str,
    *,
    context: str,
    kubeconfig: Path,
    env: Mapping[str, str] | None = None,
) -> bool:
    result = kubectl.get("namespace", name=namespace, context=context, kubeconfig=kubeconfig, env=env, check=False)
    return result.ok


def jobs_with_prefix(
    kubectl: Kubectl,
    prefix: str,
    *,
    namespace: str,
    context: str,
    kubeconfig: Path,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    result = kubectl.get(
        "jobs", namespace=namespace, context=context, kubeconfig=kubeconfig, output="name", env=env, check=False
    )
    names = [line.removeprefix("job.batch/") for line in result.stdout.splitlines() if line]
    return [name for name in names if name.startswith(prefix)]


def cleanup_failed_jobs_by_prefix(
    kubectl: Kubectl,
    prefix: str,
    *,
    namespace: str,
    context: str,
    kubeconfig: Path,
    env: Mapping[str, str] | None = None,
) -> list[str]:
    deleted: list[str] = []
    for job in jobs_with_prefix(kubectl, prefix, namespace=namespace, context=context, kubeconfig=kubeconfig, env=env):
        result = kubectl.get(
            "job",
            name=job,
            namespace=namespace,
            context=context,
            kubeconfig=kubeconfig,
            output="jsonpath={.status.failed}",
            env=env,
            check=False,
        )
        failed = result.stdout.strip()
        if failed and failed != "0":
            kubectl.delete("job", job, namespace=namespace, context=context, kubeconfig=kubeconfig, env=env)
            deleted.append(job)
    return deleted


def state_has_resource(
    terraform: Terraform,
    terraform_dir: Path,
    resource_addr: str,
    *,
    env: Mapping[str, str] | None = None,
) -> bool:
    return terraform.state_show(terraform_dir, resource_addr, env=env, check=False).ok


def import_namespace_if_missing_in_state(
    terraform: Terraform,
    kubectl: Kubectl,
    *,
    terraform_dir: Path,
    resource_addr: str,
    namespace: str,
    var_files: Sequence[Path | str] = (),
    variables: Mapping[str, str] | None = None,
    context: str,
    kubeconfig: Path,
    env: Mapping[str, str] | None = None,
) -> bool:
    if state_has_resource(terraform, terraform_dir, resource_addr, env=env):
        return False
    if not namespace_exists(kubectl, namespace, context=context, kubeconfig=kubeconfig, env=env):
        return False

    log.step(f"Importing existing namespace '{namespace}' into Terraform state...")
    terraform.import_resource(
        terraform_dir, resource_addr, namespace, var_files=var_files, variables=variables, env=env
    )
    return True
