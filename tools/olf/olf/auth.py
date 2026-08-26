"""Provider authentication state and SDK credential resolution.

This module intentionally contains no HTTP UI.  AWS returns its own device
authorization URL and Azure Identity opens Microsoft Entra's browser flow.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
import webbrowser
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
from botocore import UNSIGNED
from botocore.config import Config

from olf.deployment.errors import DeploymentPreconditionError

_AWS_SCOPE = ["sso:account:access"]
_ARM_SCOPE = "https://management.azure.com/.default"
_AWS_AUTOMATION_VARIABLES = {
    "AWS_ACCESS_KEY_ID",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
}
_AZURE_AUTOMATION_VARIABLES = {
    "AZURE_CLIENT_SECRET",
    "AZURE_CLIENT_CERTIFICATE_PATH",
    "AZURE_FEDERATED_TOKEN_FILE",
    "IDENTITY_ENDPOINT",
    "MSI_ENDPOINT",
}


class AuthenticationError(DeploymentPreconditionError):
    """Authentication is absent, expired, or cannot be refreshed."""


def _uses_aws_automation(environ: Mapping[str, str]) -> bool:
    return any(environ.get(name) for name in _AWS_AUTOMATION_VARIABLES)


_ARM_CLIENT_SECRET_VARS = ("ARM_CLIENT_ID", "ARM_TENANT_ID", "ARM_CLIENT_SECRET")
_ARM_CERTIFICATE_VARS = ("ARM_CLIENT_ID", "ARM_TENANT_ID", "ARM_CLIENT_CERTIFICATE_PATH")
_ARM_OIDC_TOKEN_VARS = ("ARM_CLIENT_ID", "ARM_TENANT_ID", "ARM_OIDC_TOKEN")
_ARM_OIDC_TOKEN_FILE_VARS = ("ARM_CLIENT_ID", "ARM_TENANT_ID", "ARM_OIDC_TOKEN_FILE_PATH")


def _uses_azure_automation(environ: Mapping[str, str]) -> bool:
    if any(environ.get(name) for name in _AZURE_AUTOMATION_VARIABLES):
        return True
    # AzureRM's provider reads these directly - it never goes through
    # azure-identity - so Terraform authenticates correctly with only ARM_*
    # set while `DefaultAzureCredential`/`EnvironmentCredential`, which only
    # recognize the differently-named AZURE_* forms, see no automation
    # source at all.
    return any(
        all(environ.get(name) for name in var_group)
        for var_group in (
            _ARM_CLIENT_SECRET_VARS,
            _ARM_CERTIFICATE_VARS,
            _ARM_OIDC_TOKEN_VARS,
            _ARM_OIDC_TOKEN_FILE_VARS,
        )
    )


def _azure_automation_credential(environ: Mapping[str, str]) -> Any | None:
    """Translate AzureRM's native ARM_* automation variables into a credential.

    Returns `None` when none of the ARM_* forms are present, so the caller
    falls back to `DefaultAzureCredential` for the AZURE_*/managed-identity
    forms `_uses_azure_automation` also recognizes.
    """
    client_id = environ.get("ARM_CLIENT_ID")
    tenant_id = environ.get("ARM_TENANT_ID")
    if not client_id or not tenant_id:
        return None
    client_secret = environ.get("ARM_CLIENT_SECRET")
    if client_secret:
        from azure.identity import ClientSecretCredential

        return ClientSecretCredential(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)
    certificate_path = environ.get("ARM_CLIENT_CERTIFICATE_PATH")
    if certificate_path:
        from azure.identity import CertificateCredential

        return CertificateCredential(tenant_id=tenant_id, client_id=client_id, certificate_path=certificate_path)
    oidc_token = environ.get("ARM_OIDC_TOKEN")
    oidc_token_file = environ.get("ARM_OIDC_TOKEN_FILE_PATH")
    if oidc_token or oidc_token_file:
        from azure.identity import ClientAssertionCredential

        def _assertion() -> str:
            if oidc_token:
                return oidc_token
            return Path(str(oidc_token_file)).read_text(encoding="utf-8").strip()

        return ClientAssertionCredential(tenant_id=tenant_id, client_id=client_id, func=_assertion)
    return None


def _aws_instance_profile_available() -> bool:
    """Detect an EC2 instance-profile credential the environment cannot show.

    Every other AWS automation source - static keys, IRSA/EKS Pod Identity's
    web identity token, an ECS/EKS-agent container role - sets an environment
    variable `_uses_aws_automation` can see. A bare EC2 instance profile,
    discovered through IMDS, sets none of them; without this check a saved
    OLF browser session would silently outrank the workload identity ADR 0030
    requires to win. Bounded to a short timeout and a single attempt: IMDS is
    unreachable (not merely absent) off EC2, and this must not add a
    multi-second stall to every interactive deploy that has a saved session.
    """
    from botocore.utils import InstanceMetadataFetcher

    try:
        return bool(InstanceMetadataFetcher(timeout=0.1, num_attempts=1).retrieve_iam_role_credentials())
    except Exception:
        return False


def _azure_managed_identity_available() -> bool:
    """Detect a system-assigned Azure managed identity the environment cannot show.

    App Service/Functions managed identity sets IDENTITY_ENDPOINT/MSI_ENDPOINT,
    already covered by `_uses_azure_automation`. A VM/VMSS system-assigned
    identity is discovered through IMDS and sets nothing - same rationale and
    latency bound as `_aws_instance_profile_available`; a token fetched here
    is simply discarded, the real one is minted fresh when actually used.
    """
    from azure.identity import ManagedIdentityCredential

    try:
        ManagedIdentityCredential(connection_timeout=1, retry_total=0).get_token(_ARM_SCOPE)
        return True
    except Exception:
        return False


def credential_selection_environment(provider: str, environ: Mapping[str, str]) -> dict[str, str]:
    """Return only cloud credential-selection variables for a child command.

    Deployment commands inherit the process environment, but SDK adapters use
    their explicit environment mapping to choose a credential source. Keeping
    this narrow avoids putting unrelated user environment values in diagnostic
    output while preserving automation precedence.
    """
    prefixes = ("AWS_",) if provider == "aws" else ("ARM_", "AZURE_", "IDENTITY_", "MSI_")
    selected = {name: value for name, value in environ.items() if name.startswith(prefixes)}
    if environ.get("OLF_HOME"):
        selected["OLF_HOME"] = environ["OLF_HOME"]
    return selected


def _sso_client(service: str, *, region: str) -> Any:
    """Create an IAM Identity Center client without resolving AWS profiles.

    Both SSO APIs use the bearer token supplied in their request, not SigV4.
    Marking them unsigned also prevents a Terraform credential_process from
    recursively invoking itself when AWS_PROFILE points at that process.

    Built from a fresh `boto3.Session()`, never the bare `boto3.client()`
    module function: that function shares one process-global default
    session, and `botocore.session.Session` permanently caches the config
    file it parses on its first use for the rest of the process's life. If
    anything earlier in the process makes a bare `boto3.client()`/
    `boto3.Session()` call before `AWS_CONFIG_FILE`/`AWS_PROFILE` are set -
    exactly what happens between resolving foundation facts and the
    artifacts phase's `_applied_authentication_environment` overlay - every
    later bare call keeps resolving profiles against the stale config file,
    raising `ProfileNotFound` for a profile the *current* `AWS_CONFIG_FILE`
    genuinely defines. Reproduced directly: a prior bare `boto3.client()`
    call followed by setting `AWS_CONFIG_FILE`/`AWS_PROFILE` breaks a
    subsequent bare call but not a fresh `boto3.Session()`.
    """
    return boto3.Session().client(service, region_name=region, config=Config(signature_version=UNSIGNED))


def auth_home(environ: Mapping[str, str] | None = None) -> Path:
    """Return the absolute OLF authentication directory.

    Resolved deliberately: these paths are handed to child processes that run
    with a different working directory - Terraform's `AWS_CONFIG_FILE` and
    credential_process, and the Azure bridge directory prepended to `PATH`
    (Terraform `-chdir` switches the process directory). A relative `OLF_HOME`
    would leave both pointing somewhere that does not exist, so managed
    authentication would fail after a successful login.
    """
    raw = (environ or os.environ).get("OLF_HOME")
    home = Path(raw).expanduser() if raw else Path.home() / ".openlakeforge"
    return (home / "auth").resolve()


def _state_path(provider: str, environ: Mapping[str, str] | None = None) -> Path:
    return auth_home(environ) / f"{provider}.json"


def load_state(provider: str, environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    path = _state_path(provider, environ)
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthenticationError(
            f"invalid {provider} authentication state; run 'olf auth login --provider {provider}'."
        ) from exc
    if not isinstance(loaded, dict):
        raise AuthenticationError(
            f"invalid {provider} authentication state; run 'olf auth login --provider {provider}'."
        )
    return loaded


def save_state(provider: str, state: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> None:
    path = _state_path(provider, environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(prefix=f".{provider}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(state), handle, sort_keys=True)
            handle.write("\n")
        os.chmod(raw_path, stat.S_IRUSR | stat.S_IWUSR)
        Path(raw_path).replace(path)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        Path(raw_path).unlink(missing_ok=True)
        raise


def clear_state(provider: str, environ: Mapping[str, str] | None = None) -> None:
    home = auth_home(environ)
    _state_path(provider, environ).unlink(missing_ok=True)
    if provider == "aws":
        (home / "aws-terraform-config").unlink(missing_ok=True)
    elif provider == "azure":
        bridge = home / "azure-terraform-bridge" / "az"
        bridge.unlink(missing_ok=True)
        if bridge.parent.exists() and not any(bridge.parent.iterdir()):
            bridge.parent.rmdir()


def _expires_at(seconds: int | float) -> str:
    return (datetime.now(UTC) + timedelta(seconds=float(seconds))).isoformat()


def _timestamp_or_duration(value: int | float) -> str:
    """Convert AWS's epoch timestamp (or a test fixture duration) to ISO 8601."""
    numeric = float(value)
    if numeric > 1_000_000_000:
        return datetime.fromtimestamp(numeric, UTC).isoformat()
    return _expires_at(numeric)


def _expired(state: Mapping[str, Any], *, skew_seconds: int = 120) -> bool:
    raw = state.get("access_expires_at")
    if not isinstance(raw, str):
        return True
    try:
        return datetime.fromisoformat(raw) <= datetime.now(UTC) + timedelta(seconds=skew_seconds)
    except ValueError:
        return True


def login_aws(
    *,
    profile: str | None = None,
    start_url: str | None = None,
    sso_region: str | None = None,
    account_id: str | None = None,
    role_name: str | None = None,
    open_browser: bool = True,
    environ: Mapping[str, str] | None = None,
    choose: Any | None = None,
) -> dict[str, Any]:
    """Authenticate through IAM Identity Center's official device flow."""
    env = environ or os.environ
    # An explicit browser configuration must win over an ambient profile;
    # this lets a user deliberately replace an expired CLI SSO session.
    chosen_profile = profile or (env.get("AWS_PROFILE") if not start_url else None)
    if chosen_profile:
        try:
            identity = boto3.Session(profile_name=chosen_profile).client("sts").get_caller_identity()
        except Exception as exc:
            raise AuthenticationError(
                f"AWS profile '{chosen_profile}' is unavailable; run 'olf auth login --provider aws' to sign in."
            ) from exc
        state = {"source": "profile", "profile": chosen_profile, "identity": identity}
        save_state("aws", state, env)
        return state

    if not start_url or not sso_region:
        raise AuthenticationError(
            "AWS IAM Identity Center requires --start-url and --sso-region the first time, "
            "or use --profile to adopt an existing AWS profile."
        )
    oidc = _sso_client("sso-oidc", region=sso_region)
    registration = oidc.register_client(clientName="openlakeforge", clientType="public", scopes=_AWS_SCOPE)
    device = oidc.start_device_authorization(
        clientId=registration["clientId"], clientSecret=registration["clientSecret"], startUrl=start_url
    )
    url = device["verificationUriComplete"]
    opened = webbrowser.open(url) if open_browser else False
    if not opened:
        print(f"Open this AWS sign-in page: {device['verificationUri']}")  # noqa: T201
        print(f"Enter code: {device['userCode']}")  # noqa: T201
    deadline = time.monotonic() + int(device["expiresIn"])
    interval = int(device.get("interval", 5))
    while True:
        if time.monotonic() >= deadline:
            raise AuthenticationError("AWS device authorization expired; run 'olf auth login --provider aws' again.")
        try:
            token = oidc.create_token(
                clientId=registration["clientId"],
                clientSecret=registration["clientSecret"],
                grantType="urn:ietf:params:oauth:grant-type:device_code",
                deviceCode=device["deviceCode"],
            )
            break
        except oidc.exceptions.AuthorizationPendingException:
            time.sleep(interval)
        except oidc.exceptions.SlowDownException:
            interval += 5
            time.sleep(interval)
        except oidc.exceptions.AccessDeniedException as exc:
            raise AuthenticationError("AWS device authorization was denied.") from exc

    sso = _sso_client("sso", region=sso_region)
    accounts = list(sso.get_paginator("list_accounts").paginate(accessToken=token["accessToken"]))
    flattened_accounts = [account for page in accounts for account in page["accountList"]]
    selected_account = _resolve_choice(account_id, flattened_accounts, "accountId", choose, "AWS account")
    roles = list(
        sso.get_paginator("list_account_roles").paginate(accessToken=token["accessToken"], accountId=selected_account)
    )
    flattened_roles = [role for page in roles for role in page["roleList"]]
    selected_role = _resolve_choice(role_name, flattened_roles, "roleName", choose, "AWS role")
    state = {
        "source": "olf-sso",
        "start_url": start_url,
        "sso_region": sso_region,
        "account_id": selected_account,
        "role_name": selected_role,
        "client_id": registration["clientId"],
        "client_secret": registration["clientSecret"],
        "client_secret_expires_at": _timestamp_or_duration(registration.get("clientSecretExpiresAt", 0)),
        "access_token": token["accessToken"],
        "access_expires_at": _expires_at(token["expiresIn"]),
        "refresh_token": token.get("refreshToken", ""),
    }
    save_state("aws", state, env)
    return state


def _resolve_choice(
    explicit: str | None, items: list[Mapping[str, Any]], key: str, choose: Any | None, label: str
) -> str:
    """Return the caller's explicit selection, or prompt for one.

    An explicit `--account-id`/`--role-name` is validated against what the
    session actually offers. Persisting an unlisted value would report
    "authentication ready" and then fail much later inside
    `get_role_credentials`, where the cause is far from obvious.
    """
    if not explicit:
        return _choose(items, key, choose, label)
    available = [str(item[key]) for item in items if item.get(key)]
    if explicit not in available:
        offered = ", ".join(sorted(available)) or "(none)"
        raise AuthenticationError(f"{label} {explicit!r} is not available for this session; offered: {offered}.")
    return explicit


def _choose(items: list[Mapping[str, Any]], key: str, choose: Any | None, label: str) -> str:
    values = [str(item[key]) for item in items if item.get(key)]
    if len(values) == 1:
        return values[0]
    if not values:
        raise AuthenticationError(f"No {label}s are available for this account.")
    if choose is None:
        raise AuthenticationError(f"Multiple {label}s are available; select one with the corresponding option.")
    return str(choose(values, label))


def aws_session(environ: Mapping[str, str], *, region: str | None = None) -> Any:
    """Return a boto3 session sourced from OLF state or normal SDK discovery."""
    if _uses_aws_automation(environ):
        # An explicit AWS_PROFILE disables botocore's environment-credential
        # provider, so injected automation keys/web-identity tokens would be
        # silently ignored. Let botocore's own chain apply normal precedence.
        return boto3.Session(region_name=region)
    state = load_state("aws", environ)
    if state is None:
        return boto3.Session(profile_name=environ.get("AWS_PROFILE"), region_name=region)
    if _aws_instance_profile_available():
        return boto3.Session(region_name=region)
    if state.get("source") == "profile":
        return boto3.Session(profile_name=str(state["profile"]), region_name=region)
    if state.get("source") != "olf-sso":
        raise AuthenticationError("unknown AWS authentication source; run 'olf auth login --provider aws'.")
    state = _refresh_aws_access_token(state, environ)
    sso = _sso_client("sso", region=str(state["sso_region"]))
    credentials = sso.get_role_credentials(
        roleName=str(state["role_name"]), accountId=str(state["account_id"]), accessToken=str(state["access_token"])
    )["roleCredentials"]
    return boto3.Session(
        aws_access_key_id=credentials["accessKeyId"],
        aws_secret_access_key=credentials["secretAccessKey"],
        aws_session_token=credentials["sessionToken"],
        region_name=region,
    )


def aws_process_credentials(environ: Mapping[str, str]) -> dict[str, Any]:
    """Return AWS `credential_process`-protocol JSON with a real expiry.

    For `olf-sso` state this calls `get_role_credentials` directly instead of
    going through `aws_session`, so the SSO API's own `expiration` (epoch
    milliseconds) reaches Terraform - that session's permission-set duration
    can be shorter than a guessed TTL, and botocore rejects credentials whose
    reported expiry has already passed. Every other source has no exposed
    expiry, so a short, conservative TTL is used instead: it is safe to be
    wrong short (Terraform just re-invokes sooner) but unsafe to be wrong long
    (botocore trusts a stale `Expiration` for its full stated duration).
    """
    state = load_state("aws", environ)
    if state is not None and state.get("source") == "olf-sso":
        state = _refresh_aws_access_token(dict(state), environ)
        sso = _sso_client("sso", region=str(state["sso_region"]))
        credentials = sso.get_role_credentials(
            roleName=str(state["role_name"]),
            accountId=str(state["account_id"]),
            accessToken=str(state["access_token"]),
        )["roleCredentials"]
        return {
            "Version": 1,
            "AccessKeyId": credentials["accessKeyId"],
            "SecretAccessKey": credentials["secretAccessKey"],
            "SessionToken": credentials["sessionToken"],
            "Expiration": datetime.fromtimestamp(credentials["expiration"] / 1000, UTC).isoformat(),
        }
    session = aws_session(environ)
    frozen = session.get_credentials().get_frozen_credentials()
    return {
        "Version": 1,
        "AccessKeyId": frozen.access_key,
        "SecretAccessKey": frozen.secret_key,
        "SessionToken": frozen.token,
        "Expiration": _expires_at(300),
    }


def _refresh_aws_access_token(state: dict[str, Any], environ: Mapping[str, str]) -> dict[str, Any]:
    if not _expired(state):
        return state
    refresh_token = state.get("refresh_token")
    if not refresh_token:
        raise AuthenticationError("AWS SSO session expired; run 'olf auth login --provider aws'.")
    oidc = _sso_client("sso-oidc", region=str(state["sso_region"]))
    try:
        token = oidc.create_token(
            clientId=str(state["client_id"]),
            clientSecret=str(state["client_secret"]),
            grantType="refresh_token",
            refreshToken=str(refresh_token),
        )
    except Exception as exc:
        raise AuthenticationError("AWS SSO session expired; run 'olf auth login --provider aws'.") from exc
    state["access_token"] = token["accessToken"]
    state["access_expires_at"] = _expires_at(token["expiresIn"])
    if token.get("refreshToken"):
        state["refresh_token"] = token["refreshToken"]
    save_state("aws", state, environ)
    return state


def login_azure(
    *,
    tenant_id: str | None = None,
    subscription_id: str | None = None,
    device_code: bool = False,
    environ: Mapping[str, str] | None = None,
    choose: Any | None = None,
) -> dict[str, Any]:
    """Authenticate with Azure Identity; it owns the Microsoft browser UI."""
    from azure.identity import DeviceCodeCredential, InteractiveBrowserCredential
    from azure.mgmt.resource.subscriptions import SubscriptionClient

    env = environ or os.environ
    options = _azure_cache_persistence_options()
    if device_code:
        credential = DeviceCodeCredential(tenant_id=tenant_id, cache_persistence_options=options)
    else:
        credential = InteractiveBrowserCredential(tenant_id=tenant_id, cache_persistence_options=options)
    record = credential.authenticate(scopes=[_ARM_SCOPE])
    subscriptions = list(SubscriptionClient(credential).subscriptions.list())
    selected_item = _resolve_subscription(subscription_id, subscriptions, choose)
    state = {
        "source": "olf-browser",
        "tenant_id": selected_item.tenant_id or tenant_id or "",
        "subscription_id": str(selected_item.subscription_id),
        "principal": getattr(record, "username", ""),
        "authentication_record": record.serialize(),
    }
    save_state("azure", state, env)
    return state


def _azure_cache_persistence_options() -> Any:
    """Return the shared Azure SDK cache policy for browser and device login.

    Azure Identity otherwise rejects persistent caches on headless Linux hosts
    without a system keyring before the device-code flow can begin. The SDK
    owns this cache; OLF persists only its authentication record and selection.
    """
    from azure.identity import TokenCachePersistenceOptions

    return TokenCachePersistenceOptions(name="openlakeforge-auth", allow_unencrypted_storage=True)


def _choose_subscription(subscriptions: list[Any], choose: Any | None) -> str:
    values = [str(item.subscription_id) for item in subscriptions if item.subscription_id]
    if len(values) == 1:
        return values[0]
    if not values:
        raise AuthenticationError("No Azure subscriptions are available for this identity.")
    if choose is None:
        raise AuthenticationError("Multiple Azure subscriptions are available; pass --subscription-id.")
    return str(choose(values, "Azure subscription"))


def _resolve_subscription(explicit: str | None, subscriptions: list[Any], choose: Any | None) -> Any:
    """Return the caller's explicit subscription, or prompt for one.

    An explicit `--subscription-id` is validated against what the identity
    actually has access to. Persisting an unlisted value would report
    "authentication ready" and then raise an unhandled `StopIteration`
    (`next()` over the now-empty match) the CLI's `except AuthenticationError`
    handler cannot catch, instead of an actionable message.
    """
    if not explicit:
        selected = _choose_subscription(subscriptions, choose)
        return next(item for item in subscriptions if str(item.subscription_id) == selected)
    match = next((item for item in subscriptions if str(item.subscription_id) == explicit), None)
    if match is None:
        available = ", ".join(sorted(str(item.subscription_id) for item in subscriptions)) or "(none)"
        raise AuthenticationError(f"Azure subscription {explicit!r} is not available; offered: {available}.")
    return match


def azure_credential(environ: Mapping[str, str]) -> Any:
    """Resolve the selected Azure credential without opening a browser."""
    from azure.identity import (
        AuthenticationRecord,
        AzureCliCredential,
        DefaultAzureCredential,
        InteractiveBrowserCredential,
    )

    if _uses_azure_automation(environ):
        arm_credential = _azure_automation_credential(environ)
        if arm_credential is not None:
            return arm_credential
        return DefaultAzureCredential(
            exclude_azure_cli_credential=True,
            exclude_interactive_browser_credential=True,
        )
    state = load_state("azure", environ)
    if state is None:
        return DefaultAzureCredential(exclude_interactive_browser_credential=True)
    if _azure_managed_identity_available():
        return DefaultAzureCredential(
            exclude_azure_cli_credential=True,
            exclude_interactive_browser_credential=True,
        )
    source = state.get("source")
    if source == "azure-cli":
        return AzureCliCredential(tenant_id=state.get("tenant_id") or None)
    if source == "olf-browser":
        record = AuthenticationRecord.deserialize(str(state["authentication_record"]))
        return InteractiveBrowserCredential(
            tenant_id=state.get("tenant_id") or None,
            authentication_record=record,
            cache_persistence_options=_azure_cache_persistence_options(),
            disable_automatic_authentication=True,
        )
    raise AuthenticationError("unknown Azure authentication source; run 'olf auth login --provider azure'.")


def selected_azure_subscription(environ: Mapping[str, str]) -> str | None:
    """Return the subscription saved by `olf auth login`, if any.

    `doctor()` builds its preflight environment from
    `credential_selection_environment` alone (no `terraform_auth_environment`
    overlay), so `ARM_SUBSCRIPTION_ID` is not guaranteed to be set even though
    a saved OLF session exists. Adapters consult this as their last fallback
    so authentication succeeds identically whether or not Terraform's overlay
    ran first.
    """
    state = load_state("azure", environ)
    if state is None:
        return None
    subscription_id = state.get("subscription_id")
    return str(subscription_id) if subscription_id else None


def terraform_auth_environment(provider: str, environ: Mapping[str, str]) -> dict[str, str]:
    """Return Terraform-only authentication overrides for managed browser state."""
    if provider == "aws" and _uses_aws_automation(environ):
        return {}
    if provider == "azure" and _uses_azure_automation(environ):
        return {}
    state = load_state(provider, environ)
    if state is None:
        return {}
    if provider == "aws" and _aws_instance_profile_available():
        return {}
    if provider == "azure" and _azure_managed_identity_available():
        return {}
    if provider == "aws" and state.get("source") == "olf-sso":
        config_path = auth_home(environ) / "aws-terraform-config"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        command = f'"{sys.executable}" -m olf.aws_credential_process'
        _write_private_text(config_path, "[profile openlakeforge]\ncredential_process = " + command + "\n")
        return {"AWS_CONFIG_FILE": str(config_path), "AWS_PROFILE": "openlakeforge"}
    if provider == "aws" and state.get("source") == "profile":
        return {"AWS_PROFILE": str(state["profile"])}
    if provider == "azure" and state.get("source") == "azure-cli":
        return {
            "ARM_SUBSCRIPTION_ID": str(state["subscription_id"]),
            "ARM_TENANT_ID": str(state.get("tenant_id", "")),
        }
    if provider == "azure" and state.get("source") == "olf-browser":
        bridge_dir = auth_home(environ) / "azure-terraform-bridge"
        bridge_dir.mkdir(parents=True, exist_ok=True)
        bridge = bridge_dir / "az"
        _write_private_text(bridge, f"#!{sys.executable}\nfrom olf.azure_bridge import main\nmain()\n")
        os.chmod(bridge, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        return {
            "PATH": f"{bridge_dir}{os.pathsep}{environ.get('PATH', os.environ.get('PATH', ''))}",
            "ARM_SUBSCRIPTION_ID": str(state["subscription_id"]),
            "ARM_TENANT_ID": str(state.get("tenant_id", "")),
        }
    return {}


def _write_private_text(path: Path, contents: str) -> None:
    fd, raw_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(contents)
        os.chmod(raw_path, stat.S_IRUSR | stat.S_IWUSR)
        Path(raw_path).replace(path)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        Path(raw_path).unlink(missing_ok=True)
        raise


def adopt_azure_cli(
    *,
    tenant_id: str | None = None,
    subscription_id: str | None = None,
    environ: Mapping[str, str] | None = None,
    choose: Any | None = None,
) -> dict[str, Any]:
    from azure.identity import AzureCliCredential
    from azure.mgmt.resource.subscriptions import SubscriptionClient

    credential = AzureCliCredential(tenant_id=tenant_id)
    subscriptions = list(SubscriptionClient(credential).subscriptions.list())
    item = _resolve_subscription(subscription_id, subscriptions, choose)
    state = {
        "source": "azure-cli",
        "tenant_id": item.tenant_id or tenant_id or "",
        "subscription_id": str(item.subscription_id),
    }
    save_state("azure", state, environ)
    return state
