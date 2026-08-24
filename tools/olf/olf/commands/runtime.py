"""Reusable provider-aware runtime contract activation for standalone commands."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def provider_contract_environment(
    *,
    provider: str,
    profile: str,
    namespace: str,
    cluster_name: str,
    kubeconfig_path: str,
) -> Iterator[None]:
    """Hydrate Terraform contracts for a command outside the full deploy flow."""
    from olf.commands.deployment import _build_context, _build_engine
    from olf.deployment import contract_env
    from olf.deployment.local.artifacts import applied_contract_environment
    from olf.deployment.local.provider import LocalProvider

    context = _build_context(
        provider,
        profile=profile,
        namespace=namespace,
        cluster_name=cluster_name,
        kubeconfig_path=kubeconfig_path,
    )
    deployment_provider = _build_engine(context, var_file="").provider
    if isinstance(deployment_provider, LocalProvider):
        with applied_contract_environment(deployment_provider.config):
            yield
        return
    facts = deployment_provider._foundation_facts  # noqa: SLF001 - shared standalone command context.
    with contract_env.applied_contract_environment(
        contract_terraform_dir=context.paths.platform_terraform_dir,
        repo_root=context.paths.repo_root,
        namespace=context.namespace,
        kube_context=facts.kube_context,
        kubeconfig_path=context.paths.kubeconfig_path,
        port_forward_log_prefix=context.paths.port_forward_log_prefix,
    ):
        yield
