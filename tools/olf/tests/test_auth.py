from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from olf import auth
from olf.azure_bridge import main as azure_bridge_main
from olf.cli import app


class _Pager:
    def __init__(self, pages):  # noqa: ANN001
        self.pages = pages

    def paginate(self, **_kwargs):  # noqa: ANN003, ANN202
        return self.pages


class _Oidc:
    class exceptions:
        class AuthorizationPendingException(Exception): ...

        class SlowDownException(Exception): ...

        class AccessDeniedException(Exception): ...

    def register_client(self, **_kwargs):  # noqa: ANN003, ANN202
        return {"clientId": "client", "clientSecret": "secret", "clientSecretExpiresAt": 3600}

    def start_device_authorization(self, **_kwargs):  # noqa: ANN003, ANN202
        return {
            "verificationUri": "https://device.aws",
            "verificationUriComplete": "https://device.aws/?code=abc",
            "userCode": "ABC",
            "deviceCode": "device",
            "expiresIn": 60,
            "interval": 1,
        }

    def create_token(self, **_kwargs):  # noqa: ANN003, ANN202
        return {"accessToken": "access", "refreshToken": "refresh", "expiresIn": 3600}


class _Sso:
    def get_paginator(self, name: str):  # noqa: ANN201
        if name == "list_accounts":
            return _Pager([{"accountList": [{"accountId": "123"}]}])
        return _Pager([{"roleList": [{"roleName": "Administrator"}]}])


class _PollingOidc(_Oidc):
    def __init__(self, outcomes):  # noqa: ANN001
        self.outcomes = iter(outcomes)

    def create_token(self, **_kwargs):  # noqa: ANN003, ANN202
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_aws_login_opens_only_the_aws_provided_url_and_saves_private_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    opened: list[str] = []
    monkeypatch.setenv("OLF_HOME", str(tmp_path))
    monkeypatch.setattr(auth.boto3, "client", lambda service, **_kwargs: _Oidc() if service == "sso-oidc" else _Sso())
    monkeypatch.setattr(auth.webbrowser, "open", lambda url: opened.append(url) or True)

    state = auth.login_aws(start_url="https://portal.awsapps.com/start", sso_region="eu-west-1")

    assert opened == ["https://device.aws/?code=abc"]
    assert state["account_id"] == "123"
    saved = tmp_path / "auth" / "aws.json"
    assert saved.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO) == 0
    assert auth.load_state("aws")["refresh_token"] == "refresh"


def test_aws_login_without_browser_prints_only_aws_device_details(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setenv("OLF_HOME", str(tmp_path))
    monkeypatch.setattr(auth.boto3, "client", lambda service, **_kwargs: _Oidc() if service == "sso-oidc" else _Sso())

    auth.login_aws(start_url="https://portal.awsapps.com/start", sso_region="eu-west-1", open_browser=False)

    output = capsys.readouterr().out
    assert "https://device.aws" in output
    assert "ABC" in output


def test_aws_device_polling_retries_pending_and_slowdown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OLF_HOME", str(tmp_path))
    oidc = _PollingOidc(
        [
            _Oidc.exceptions.AuthorizationPendingException(),
            _Oidc.exceptions.SlowDownException(),
            {"accessToken": "access", "refreshToken": "refresh", "expiresIn": 3600},
        ]
    )
    pauses: list[int] = []
    monkeypatch.setattr(auth.boto3, "client", lambda service, **_kwargs: oidc if service == "sso-oidc" else _Sso())
    monkeypatch.setattr(auth.time, "sleep", pauses.append)

    auth.login_aws(start_url="https://portal.awsapps.com/start", sso_region="eu-west-1", open_browser=False)

    assert pauses == [1, 6]


def test_aws_device_polling_reports_denial(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OLF_HOME", str(tmp_path))
    oidc = _PollingOidc([_Oidc.exceptions.AccessDeniedException()])
    monkeypatch.setattr(auth.boto3, "client", lambda service, **_kwargs: oidc if service == "sso-oidc" else _Sso())

    with pytest.raises(auth.AuthenticationError, match="denied"):
        auth.login_aws(start_url="https://portal.awsapps.com/start", sso_region="eu-west-1", open_browser=False)


def test_auth_status_redacts_tokens_and_logout_only_removes_olf_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OLF_HOME", str(tmp_path))
    auth.save_state("aws", {"source": "olf-sso", "access_token": "access", "refresh_token": "refresh"})
    runner = CliRunner()

    status = runner.invoke(app, ["auth", "status", "--provider", "aws"])
    logout = runner.invoke(app, ["auth", "logout", "--provider", "aws"])

    assert status.exit_code == 0
    assert "access" not in status.output
    assert logout.exit_code == 0
    assert auth.load_state("aws") is None


def test_terraform_auth_environment_keeps_automation_ahead_of_saved_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OLF_HOME", str(tmp_path))
    auth.save_state("aws", {"source": "olf-sso", "access_token": "access"})

    assert auth.terraform_auth_environment("aws", {"OLF_HOME": str(tmp_path), "AWS_ACCESS_KEY_ID": "automation"}) == {}


def test_credential_selection_environment_excludes_unrelated_values() -> None:
    assert auth.credential_selection_environment(
        "aws",
        {
            "AWS_WEB_IDENTITY_TOKEN_FILE": "/tmp/token",
            "AWS_ROLE_ARN": "arn:aws:iam::123:role/ci",
            "OLF_HOME": "/tmp/olf-home",
            "HOME": "/home",
        },
    ) == {
        "AWS_WEB_IDENTITY_TOKEN_FILE": "/tmp/token",
        "AWS_ROLE_ARN": "arn:aws:iam::123:role/ci",
        "OLF_HOME": "/tmp/olf-home",
    }


def test_terraform_auth_environment_propagates_adopted_provider_selections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OLF_HOME", str(tmp_path))
    env = {"OLF_HOME": str(tmp_path)}
    auth.save_state("aws", {"source": "profile", "profile": "company-sso"}, env)
    auth.save_state("azure", {"source": "azure-cli", "subscription_id": "sub-id", "tenant_id": "tenant-id"}, env)

    assert auth.terraform_auth_environment("aws", env) == {"AWS_PROFILE": "company-sso"}
    assert auth.terraform_auth_environment("azure", env) == {
        "ARM_SUBSCRIPTION_ID": "sub-id",
        "ARM_TENANT_ID": "tenant-id",
    }


def test_azure_terraform_bridge_supports_only_azurerm_command_contract(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    subscription = type(
        "Subscription",
        (),
        {"subscription_id": "sub-id", "display_name": "Sandbox", "tenant_id": "tenant-id", "state": "Enabled"},
    )()
    credential = type(
        "Credential", (), {"get_token": lambda *_args: type("Token", (), {"token": "secret", "expires_on": 1})()}
    )()
    import olf.azure_bridge as bridge

    monkeypatch.setattr(
        bridge,
        "load_state",
        lambda *_args: {"source": "olf-browser", "subscription_id": "sub-id", "tenant_id": "tenant-id"},
    )
    monkeypatch.setattr(bridge, "azure_credential", lambda *_args: credential)
    monkeypatch.setattr(
        bridge,
        "SubscriptionClient",
        lambda *_args: type(
            "Client", (), {"subscriptions": type("Subscriptions", (), {"list": lambda *_args: [subscription]})()}
        )(),
    )
    monkeypatch.setattr("sys.argv", ["az", "account", "show", "-o=json"])

    azure_bridge_main()

    assert json.loads(capsys.readouterr().out)["id"] == "sub-id"


def test_azure_browser_login_delegates_to_azure_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import azure.identity
    import azure.mgmt.resource.subscriptions

    monkeypatch.setenv("OLF_HOME", str(tmp_path))
    calls: list[object] = []
    record = type("Record", (), {"serialize": lambda *_args: "record", "username": "user@example.com"})()
    credential = type("Credential", (), {"authenticate": lambda *_args, **_kwargs: calls.append(True) or record})()
    subscription = type(
        "Subscription",
        (),
        {"subscription_id": "sub-id", "display_name": "Sandbox", "tenant_id": "tenant-id", "state": "Enabled"},
    )()
    monkeypatch.setattr(azure.identity, "InteractiveBrowserCredential", lambda **_kwargs: credential)
    monkeypatch.setattr(
        azure.mgmt.resource.subscriptions,
        "SubscriptionClient",
        lambda *_args: type(
            "Client", (), {"subscriptions": type("Subscriptions", (), {"list": lambda *_args: [subscription]})()}
        )(),
    )

    state = auth.login_azure()

    assert calls == [True]
    assert state["principal"] == "user@example.com"
