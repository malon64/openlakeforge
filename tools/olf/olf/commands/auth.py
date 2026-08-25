"""Interactive, SDK-managed cloud authentication commands."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from typing import Any

import typer

from olf.auth import (
    AuthenticationError,
    adopt_azure_cli,
    aws_session,
    azure_credential,
    clear_state,
    load_state,
    login_aws,
    login_azure,
)
from olf.commands._shared import fail

app = typer.Typer(help="Authenticate cloud providers through their official SDK flows.", no_args_is_help=True)


def _select(values: list[str], label: str) -> str:
    typer.echo(f"Available {label}s:")
    for index, value in enumerate(values, start=1):
        typer.echo(f"  {index}. {value}")
    choice = typer.prompt(f"Select {label}", type=int)
    if choice < 1 or choice > len(values):
        raise AuthenticationError(f"invalid {label} selection")
    return values[choice - 1]


@app.command()
def login(
    provider: str = typer.Option(..., "--provider", help="aws or azure."),
    profile: str = typer.Option("", "--profile", help="Existing AWS profile to adopt."),
    start_url: str = typer.Option("", "--start-url", help="AWS IAM Identity Center start URL."),
    sso_region: str = typer.Option("", "--sso-region", help="AWS IAM Identity Center region."),
    account_id: str = typer.Option("", "--account-id", help="AWS account ID."),
    role_name: str = typer.Option("", "--role-name", help="AWS role name."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Print AWS device URL instead of opening it."),
    tenant_id: str = typer.Option("", "--tenant-id", help="Azure tenant ID."),
    subscription_id: str = typer.Option("", "--subscription-id", help="Azure subscription ID."),
    device_code: bool = typer.Option(False, "--device-code", help="Use Azure device-code authentication."),
) -> None:
    """Authenticate through AWS IAM Identity Center or Microsoft Entra."""
    try:
        if provider == "aws":
            if not profile and not start_url:
                # A shared default profile is the least surprising reuse path.
                # If it is absent or expired, collect the IAM Identity Center
                # coordinates interactively; deploy itself never prompts.
                try:
                    state = login_aws(profile=os.environ.get("AWS_PROFILE") or "default")
                    typer.echo(f"AWS authentication ready ({state['source']}).")
                    return
                except Exception:
                    start_url = typer.prompt("AWS IAM Identity Center start URL")
                    sso_region = typer.prompt("AWS IAM Identity Center region")
            elif start_url and not sso_region:
                sso_region = typer.prompt("AWS IAM Identity Center region")
            state = login_aws(
                profile=profile or None,
                start_url=start_url or None,
                sso_region=sso_region or None,
                account_id=account_id or None,
                role_name=role_name or None,
                open_browser=not no_browser,
                choose=_select,
            )
            typer.echo(f"AWS authentication ready ({state['source']}).")
        elif provider == "azure":
            if not device_code and not tenant_id and shutil.which("az"):
                try:
                    state = adopt_azure_cli(subscription_id=subscription_id or None, choose=_select)
                    typer.echo(f"Azure CLI session selected for subscription {state['subscription_id']}.")
                    return
                except Exception:  # A missing/expired optional CLI session simply falls through to browser login.
                    pass
            state = login_azure(
                tenant_id=tenant_id or None,
                subscription_id=subscription_id or None,
                device_code=device_code,
                choose=_select,
            )
            typer.echo(f"Azure authentication ready for subscription {state['subscription_id']}.")
        else:
            raise AuthenticationError("--provider must be 'aws' or 'azure'.")
    except AuthenticationError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc


@app.command()
def status(provider: str = typer.Option(..., "--provider", help="aws or azure.")) -> None:
    """Show the selected authentication source without printing secrets."""
    try:
        if provider not in {"aws", "azure"}:
            raise AuthenticationError("--provider must be 'aws' or 'azure'.")
        state = load_state(provider)
        if state is None:
            raise AuthenticationError(
                f"No {provider} authentication is configured. Run 'olf auth login --provider {provider}'."
            )
        safe = {
            key: value for key, value in state.items() if key not in {"access_token", "refresh_token", "client_secret"}
        }
        if provider == "aws":
            identity = safe.get("identity", {})
            if isinstance(identity, dict):
                safe["principal"] = identity.get("Arn") or safe.get("role_name", "unknown")
                safe["account"] = identity.get("Account") or safe.get("account_id", "unknown")
            else:
                safe["principal"] = safe.get("role_name", "unknown")
                safe["account"] = safe.get("account_id", "unknown")
            safe["expiry"] = safe.get("access_expires_at", "managed by AWS SDK")
        else:
            token = azure_credential(os.environ).get_token("https://management.azure.com/.default")
            safe["principal"] = safe.get("principal", "managed by Azure SDK")
            safe["subscription"] = safe.get("subscription_id", "unknown")
            safe["expiry"] = datetime.fromtimestamp(token.expires_on, UTC).isoformat()
        typer.echo(json.dumps(safe, indent=2, sort_keys=True))
    except AuthenticationError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc


@app.command()
def logout(provider: str = typer.Option(..., "--provider", help="aws or azure.")) -> None:
    """Remove OLF-owned authentication state without touching vendor caches."""
    if provider not in {"aws", "azure"}:
        raise typer.Exit(code=fail("--provider must be 'aws' or 'azure'."))
    clear_state(provider)
    typer.echo(f"Removed OLF-managed {provider} authentication state.")


@app.command(hidden=True)
def credentials(provider: str = typer.Option(..., "--provider")) -> None:
    """Emit short-lived credentials for Terraform's generated credential_process."""
    if provider != "aws":
        raise typer.Exit(code=fail("credential_process is only available for AWS."))
    try:
        credentials = aws_session(os.environ).get_credentials()
        frozen = credentials.get_frozen_credentials()
        expiry = getattr(credentials, "_expiry_time", None)
        payload: dict[str, Any] = {
            "Version": 1,
            "AccessKeyId": frozen.access_key,
            "SecretAccessKey": frozen.secret_key,
            "SessionToken": frozen.token,
        }
        if expiry is not None:
            payload["Expiration"] = expiry.astimezone(UTC).isoformat().replace("+00:00", "Z")
        else:
            payload["Expiration"] = (datetime.now(UTC).replace(microsecond=0)).isoformat().replace("+00:00", "Z")
        typer.echo(json.dumps(payload))
    except AuthenticationError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc
