from __future__ import annotations

import json
from pathlib import Path

from _tooling_support import RecordingRunner

from olf.tooling.aws import AwsCli
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver


def _aws(result: CommandResult | None = None) -> tuple[AwsCli, RecordingRunner]:
    runner = RecordingRunner(result)
    resolver = PathExecutableResolver(overrides={"aws": Path("aws")})
    return AwsCli(runner, resolver), runner


def test_eks_update_kubeconfig_builds_exact_argv() -> None:
    aws, runner = _aws()
    kubeconfig = Path("/repo/.tmp/kubeconfigs/aws.yaml")

    aws.eks_update_kubeconfig(
        "eks-openlakeforge-poc",
        region="eu-west-1",
        kubeconfig_path=kubeconfig,
        alias="eks-openlakeforge-poc",
    )

    assert runner.last_call.argv == [
        "aws",
        "eks",
        "update-kubeconfig",
        "--region",
        "eu-west-1",
        "--name",
        "eks-openlakeforge-poc",
        "--kubeconfig",
        str(kubeconfig),
        "--alias",
        "eks-openlakeforge-poc",
    ]


def test_sts_get_caller_identity_parses_json() -> None:
    payload = {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/dev"}
    aws, runner = _aws(
        CommandResult(argv=(), returncode=0, stdout=json.dumps(payload), stderr="", duration_seconds=0.0)
    )

    identity = aws.sts_get_caller_identity()

    assert identity == payload
    assert runner.last_call.argv == ["aws", "sts", "get-caller-identity", "--output", "json"]


def test_ecr_get_login_password_strips_output() -> None:
    aws, runner = _aws(
        CommandResult(argv=(), returncode=0, stdout="password-value\n", stderr="", duration_seconds=0.0)
    )

    password = aws.ecr_get_login_password(region="eu-west-1")

    assert password == "password-value"
    assert runner.last_call.argv == ["aws", "ecr", "get-login-password", "--region", "eu-west-1"]
