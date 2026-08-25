"""AWS credential_process entry point used only by Terraform's scoped profile."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from olf.auth import aws_session


def main() -> None:
    session = aws_session(__import__("os").environ)
    credentials = session.get_credentials()
    frozen = credentials.get_frozen_credentials()
    expiry = getattr(credentials, "_expiry_time", None) or datetime.now(UTC) + timedelta(minutes=50)
    print(  # noqa: T201 - credential_process protocol requires stdout JSON.
        json.dumps(
            {
                "Version": 1,
                "AccessKeyId": frozen.access_key,
                "SecretAccessKey": frozen.secret_key,
                "SessionToken": frozen.token,
                "Expiration": expiry.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            }
        )
    )


if __name__ == "__main__":
    main()
