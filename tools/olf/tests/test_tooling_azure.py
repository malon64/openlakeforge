from __future__ import annotations

import json
from pathlib import Path

from _tooling_support import RecordingRunner

from olf.tooling.azure import AzureCli
from olf.tooling.process import CommandResult
from olf.tooling.resolver import PathExecutableResolver


def _azure(result: CommandResult | None = None) -> tuple[AzureCli, RecordingRunner]:
    runner = RecordingRunner(result)
    resolver = PathExecutableResolver(overrides={"az": Path("az")})
    return AzureCli(runner, resolver), runner


def test_aks_get_credentials_builds_exact_argv() -> None:
    azure, runner = _azure()
    kubeconfig = Path("/repo/.tmp/kubeconfigs/azure.yaml")

    azure.aks_get_credentials(
        "aks-openlakeforge-poc",
        resource_group="openlakeforge-poc-rg",
        kubeconfig_path=kubeconfig,
    )

    assert runner.last_call.argv == [
        "az",
        "aks",
        "get-credentials",
        "--resource-group",
        "openlakeforge-poc-rg",
        "--name",
        "aks-openlakeforge-poc",
        "--file",
        str(kubeconfig),
        "--overwrite-existing",
    ]


def test_account_show_parses_json() -> None:
    payload = {"id": "sub-id", "name": "OpenLakeForge"}
    azure, runner = _azure(
        CommandResult(argv=(), returncode=0, stdout=json.dumps(payload), stderr="", duration_seconds=0.0)
    )

    account = azure.account_show()

    assert account == payload
    assert runner.last_call.argv == ["az", "account", "show", "--output", "json"]


def test_acr_login_builds_expected_argv() -> None:
    azure, runner = _azure()

    azure.acr_login("openlakeforgeacr")

    assert runner.last_call.argv == ["az", "acr", "login", "--name", "openlakeforgeacr"]


def test_account_set_builds_expected_argv() -> None:
    azure, runner = _azure()

    azure.account_set("sub-id")

    assert runner.last_call.argv == ["az", "account", "set", "--subscription", "sub-id"]


def test_aks_show_builds_exact_argv_and_defaults_to_no_check() -> None:
    azure, runner = _azure(CommandResult(argv=(), returncode=1, stdout="", stderr="not found", duration_seconds=0.0))

    result = azure.aks_show("aks-openlakeforge-poc", resource_group="openlakeforge-poc-rg")

    assert result.returncode == 1
    assert runner.last_call.argv == [
        "az",
        "aks",
        "show",
        "--resource-group",
        "openlakeforge-poc-rg",
        "--name",
        "aks-openlakeforge-poc",
    ]
    assert runner.last_call.kwargs["check"] is False
