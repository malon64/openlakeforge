"""Asserts the Makefile's `local-*`/`azure-*`/`aws-*` targets are pure `olf` delegates.

Item 10 of issue #124 (local) and issue #125 (AWS/Azure) require that Make
contain no Terraform/Docker/kubectl/Helm/deployment-shell orchestration -
every deployment-lifecycle target's recipe must be a single `olf` (or a
`$(MAKE)` re-invocation of another deployment target) delegation.

Verified behaviorally via `make -n <target>` (GNU Make's own recipe
expansion, run with `--just-print` so nothing actually executes) rather
than by inspecting the Makefile's raw text: AGENTS.md's "Python for
behaviour, shell for structure" rule explicitly forbids assertions that
grep source text, and a raw-text check is also blind to indirection - a
recipe written as `TF = terraform` then `$(TF) apply` would read clean to
a substring search while still shelling out to Terraform. `make -n`
expands every variable exactly as a real invocation would and prints the
final command line, closing that gap; `$(MAKE)` re-invocations recurse
automatically under `-n` (GNU Make propagates `-n` to sub-makes via
MAKEFLAGS), so a `local-slim-up`-style wrapper is verified all the way
through to its terminal `olf` invocation.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FORBIDDEN_TOKENS = (
    " terraform",  # leading space: a real invocation ("terraform apply"), not the
    # "infra/terraform/environments/..." contract-dir path e2e targets legitimately reference
    "docker ",
    "kubectl",
    "helm ",
    "bash scripts/local",
    "bash scripts/lib",
    "bash scripts/aws",
    "bash scripts/azure",
    "bash scripts/artifacts/olf.sh",
)
_TARGET_PREFIX_PATTERN = r"^((?:local|azure|aws)-[A-Za-z0-9_-]+):"


def _deployment_target_names() -> list[str]:
    """Enumerate target names only - no assertions are made on this text.

    Behavior is verified separately via `make -n`; this just answers "which
    targets exist" so every one can be dry-run.
    """
    makefile_text = (_REPO_ROOT / "Makefile").read_text()
    return re.findall(_TARGET_PREFIX_PATTERN, makefile_text, re.MULTILINE)


def _dry_run(target: str, *variables: str) -> str:
    """`make -n <target>`: GNU Make's real recipe expansion, executing nothing."""
    result = subprocess.run(
        ["make", "-n", target, *variables],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_every_deployment_target_delegates_to_olf_or_another_deployment_target() -> None:
    names = _deployment_target_names()
    assert names, "expected at least one local-*/azure-*/aws-* Makefile target"
    assert any(name.startswith("azure-") for name in names)
    assert any(name.startswith("aws-") for name in names)

    for name in names:
        expanded = _dry_run(name)
        assert "uv run --project tools/olf --locked olf " in expanded or re.search(
            r"\bmake\b.* (local|azure|aws)-", expanded
        ), f"{name}: dry-run recipe has no olf/deployment-target delegation:\n{expanded}"
        for token in _FORBIDDEN_TOKENS:
            assert token not in expanded, f"{name}: dry-run recipe still orchestrates via {token!r}:\n{expanded}"


def test_status_targets_exist_for_every_provider() -> None:
    """`scripts/README.md` advertises `*-status` for all three providers -
    `local-status` predates this PR, but `azure-status`/`aws-status` must
    also exist or the documented `olf deploy|destroy|status|forward
    --provider local|aws|azure` surface would be a lie for `status`.
    """
    names = _deployment_target_names()

    assert "local-status" in names
    assert "azure-status" in names
    assert "aws-status" in names


def test_kubeconfig_compatibility_arguments_preserve_whitespace() -> None:
    """Make delegates preserve a caller-provided kubeconfig as one CLI argument."""
    variable_by_provider = {
        "local": "LOCAL_KUBECONFIG_PATH",
        "azure": "AZURE_KUBECONFIG_PATH",
        "aws": "AWS_KUBECONFIG_PATH",
    }
    expected_path = "/tmp/open lakeforge/kubeconfig.yaml"

    for target in _deployment_target_names():
        provider = target.split("-", maxsplit=1)[0]
        expanded = _dry_run(target, f"{variable_by_provider[provider]}={expected_path}")
        if "--kubeconfig-path" in expanded:
            assert f'--kubeconfig-path "{expected_path}"' in expanded, f"{target}: {expanded}"


def test_local_slim_e2e_preserves_the_requested_complete_suite() -> None:
    expanded = _dry_run("local-slim-e2e", "E2E_SUITE=full")

    assert "olf e2e run --env local --suite full" in expanded


def test_local_terraform_targets_forward_custom_var_file() -> None:
    expected_path = "/tmp/open lakeforge/custom.tfvars"
    terraform_targets = (
        "local-platform-up",
        "local-up",
        "local-slim-platform-up",
        "local-slim-up",
        "local-slim-down",
        "local-down",
        "local-platform-down",
    )

    for target in terraform_targets:
        expanded = _dry_run(target, f"LOCAL_TFVARS_FILE={expected_path}")
        assert f'--var-file "{expected_path}"' in expanded, f"{target}: {expanded}"


def test_e2e_delegates_forward_deployment_scope() -> None:
    expected_contract_dirs = {
        "local": "infra/terraform/environments/local",
        "azure": "infra/terraform/environments/azure-poc",
        "aws": "infra/terraform/environments/aws-poc",
    }
    scopes = {
        "local": (
            "NAMESPACE=custom-namespace",
            "CLUSTER_NAME=custom-kind",
            "KUBE_CONTEXT=custom-context",
            "LOCAL_KUBECONFIG_PATH=/tmp/custom local.yaml",
        ),
        "azure": (
            "NAMESPACE=custom-namespace",
            "AZURE_CLUSTER_NAME=custom-aks",
            "AZURE_KUBECONFIG_PATH=/tmp/custom azure.yaml",
        ),
        "aws": (
            "NAMESPACE=custom-namespace",
            "AWS_REGION=eu-west-3",
            "AWS_CLUSTER_NAME=custom-eks",
            "AWS_KUBECONFIG_PATH=/tmp/custom aws.yaml",
        ),
    }

    for provider, variables in scopes.items():
        expanded = _dry_run(f"{provider}-e2e", *variables)
        assert 'NAMESPACE="custom-namespace"' in expanded
        assert f"OPENLAKEFORGE_CONTRACT_TERRAFORM_DIR={expected_contract_dirs[provider]}" in expanded

    for target in ("local-e2e", "local-slim-e2e"):
        local = _dry_run(target, *scopes["local"])
        assert 'CLUSTER_NAME="custom-kind"' in local
        assert 'KUBE_CONTEXT="custom-context"' in local
        assert 'KUBECONFIG="/tmp/custom local.yaml"' in local

    azure = _dry_run("azure-e2e", *scopes["azure"])
    assert 'AZURE_CLUSTER_NAME="custom-aks"' in azure
    assert 'KUBECONFIG="/tmp/custom azure.yaml"' in azure

    aws = _dry_run("aws-e2e", *scopes["aws"])
    assert 'AWS_REGION="eu-west-3"' in aws
    assert 'AWS_CLUSTER_NAME="custom-eks"' in aws
    assert 'KUBECONFIG="/tmp/custom aws.yaml"' in aws


def test_cloud_compatibility_targets_forward_provider_image_overrides() -> None:
    for provider in ("azure", "aws"):
        image_tag = f"{provider}-custom"
        expanded = _dry_run(f"{provider}-platform-up", f"{provider.upper()}_PROJECT_CODE_IMAGE_TAG={image_tag}")

        assert f'{provider.upper()}_PROJECT_CODE_IMAGE_TAG="{image_tag}"' in expanded
