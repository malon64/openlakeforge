"""Regression guard for the PostgreSQL bootstrap template's cross-stage
`CONNECT` isolation (#225).

Postgres grants `CONNECT` on every new database to `PUBLIC` by default. Any
role that can authenticate to the shared server - e.g. `dagster_dev`'s
credential - could reach `dagster_prod` unless `create_database_if_missing`
explicitly revokes it and grants `CONNECT` back to the owning role only.

This can't be verified end-to-end without a cluster (see the issue), so this
asserts the rendered *shape* of the template instead: the revoke/grant pair
is emitted inside the one function every provisioned database - every stage's
Dagster and Superset database, plus Polaris - routes through.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATE = (
    Path(__file__).resolve().parents[3]
    / "infra/terraform/modules/storage/postgresql/templates/init.sh.tftpl"
)


def _create_database_body() -> str:
    text = TEMPLATE.read_text()
    match = re.search(r"create_database_if_missing\(\) \{\n(.*?)\n\}\n", text, re.DOTALL)
    assert match, "create_database_if_missing() function not found in template"
    return match.group(1)


def test_create_database_revokes_public_connect_and_grants_the_owner() -> None:
    body = _create_database_body()

    assert 'REVOKE CONNECT ON DATABASE \\"$db_name\\" FROM PUBLIC' in body
    assert 'GRANT CONNECT ON DATABASE \\"$db_name\\" TO \\"$owner_name\\"' in body

    # A regression that deletes the revoke but keeps the grant (or vice
    # versa) would still leave every other stage able to connect, so both
    # halves of the pair are required, not just one.
    revoke_at = body.index('REVOKE CONNECT ON DATABASE \\"$db_name\\" FROM PUBLIC')
    grant_at = body.index('GRANT CONNECT ON DATABASE \\"$db_name\\" TO \\"$owner_name\\"')
    assert revoke_at < grant_at, "REVOKE must run before the explicit GRANT back to the owner"


def test_every_provisioned_database_routes_through_create_database_if_missing() -> None:
    text = TEMPLATE.read_text()

    # Every stage's dagster_<stage>/superset_<stage> database is provisioned
    # through this loop over database_env_prefixes, not a one-off call.
    loop = re.search(r"%\{ for prefix in database_env_prefixes ~\}(.*?)%\{ endfor ~\}", text, re.DOTALL)
    assert loop, "database_env_prefixes loop not found in template"
    assert "create_database_if_missing" in loop.group(1)

    # Polaris shares the same server and the same exposure; it must go
    # through the same function rather than a separate, unrevoked path.
    assert 'create_database_if_missing "$POLARIS_DB_NAME" "$POLARIS_DB_USER"' in text
