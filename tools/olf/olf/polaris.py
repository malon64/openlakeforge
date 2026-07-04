"""Polaris service-principal credential preflight.

Replaces the curl-based OAuth token check previously inlined (and duplicated)
in the local and Azure infra-up scripts. When Polaris restarts with in-memory
persistence, previously minted client credentials go stale (HTTP 401), and the
Terraform bootstrap must be regenerated. The HTTP mapping lives here so the
shell only reacts to a well-defined exit code.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

# Distinct exit code so callers can tell "credentials are stale, rebootstrap"
# apart from a generic failure.
STALE_EXIT_CODE = 3


def request_token_status(
    token_uri: str,
    client_id: str,
    client_secret: str,
    scope: str,
    *,
    timeout: float = 10.0,
) -> int:
    """POST an OAuth client-credentials grant and return the HTTP status code.

    Returns 0 for a transport-level failure (unreachable), so callers treat it
    as "unknown, leave bootstrap generation unchanged".
    """
    data = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        token_uri,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - localhost forward
            return response.status
    except urllib.error.HTTPError as err:
        return err.code
    except (urllib.error.URLError, OSError):
        return 0
