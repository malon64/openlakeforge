"""Unit tests for `libs/floe_revision.py`.

`libs.product_dagster` (the module this feeds) imports Dagster and Floe, and
only resolves in the isolated dependency environment `olf check
project-code` builds -- not in this test environment. `libs/floe_revision.py`
is kept free of those imports specifically so its revision-preference logic
is unit-testable here. See ADR 0012.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from libs.floe_revision import ArtifactRevisionError, built_manifest_revision, revision_digest  # noqa: E402

_VALID_REVISION = "sha256:" + "a" * 64


def test_runtime_revision_wins_over_the_baked_value() -> None:
    environ = {
        "OPENLAKEFORGE_FLOE_MANIFEST_REVISION": _VALID_REVISION,
        "OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT": "sha256:" + "b" * 64,
    }

    assert built_manifest_revision(environ) == _VALID_REVISION


def test_falls_back_to_the_baked_value_when_runtime_is_unset() -> None:
    environ = {"OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT": _VALID_REVISION}

    assert built_manifest_revision(environ) == _VALID_REVISION


def test_falls_back_to_the_baked_value_when_runtime_is_the_manual_sentinel() -> None:
    environ = {
        "OPENLAKEFORGE_FLOE_MANIFEST_REVISION": "manual",
        "OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT": _VALID_REVISION,
    }

    assert built_manifest_revision(environ) == _VALID_REVISION


def test_returns_none_when_neither_variable_is_set() -> None:
    assert built_manifest_revision({}) is None


def test_returns_none_when_both_are_the_manual_sentinel() -> None:
    environ = {
        "OPENLAKEFORGE_FLOE_MANIFEST_REVISION": "manual",
        "OPENLAKEFORGE_FLOE_MANIFEST_REVISION_BUILT": "manual",
    }

    assert built_manifest_revision(environ) is None


def test_an_invalid_runtime_revision_fails_closed() -> None:
    with pytest.raises(ArtifactRevisionError, match="OPENLAKEFORGE_FLOE_MANIFEST_REVISION"):
        built_manifest_revision({"OPENLAKEFORGE_FLOE_MANIFEST_REVISION": "not-a-revision"})


def test_revision_digest_extracts_the_hex_digest() -> None:
    assert revision_digest(_VALID_REVISION) == "a" * 64
