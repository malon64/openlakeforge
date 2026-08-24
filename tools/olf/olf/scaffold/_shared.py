"""Shared types and identifier rules for the scaffold engine."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class ScaffoldError(ValueError):
    """Raised when scaffold intent fails validation. Nothing is written."""


def require_identifier(value: str, *, field: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value):
        raise ScaffoldError(f"{field} {value!r} must match '^[a-z][a-z0-9_]*$'")
    return value


def title_case(identifier: str) -> str:
    return identifier.replace("_", " ").title()


def yaml_dq(value: str) -> str:
    """Render `value` as a double-quoted YAML scalar.

    User-supplied strings (--display-name, inferred CSV column names) can
    contain YAML metacharacters (':', '#', quotes); splicing them unquoted
    into generated YAML would corrupt or break parsing of the descriptor.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


@dataclass(frozen=True)
class ScaffoldFile:
    """One new file to write, relative to the repository root."""

    relative_path: str
    content: str


@dataclass(frozen=True)
class ScaffoldPlan:
    """A fully-resolved set of file writes plus the updated lakehouse.yaml.

    ``files`` are brand-new paths -- the commit engine refuses to write any
    of them if the path already exists. ``edits`` are pre-existing files
    (e.g. an existing domain's Floe contract gaining a new entity) that are
    replaced with new, verified content; they are exempt from the
    already-exists check because replacing them *is* the operation.
    """

    files: tuple[ScaffoldFile, ...]
    lakehouse_yaml: str
    summary: tuple[str, ...]
    edits: tuple[ScaffoldFile, ...] = ()


def repo_root_from_cwd(path: str = ".") -> Path:
    return Path(path).resolve()


def parse_source_resource(value: str, *, flag: str = "--input") -> tuple[str, str]:
    """Parse a '<source>/<resource>' CLI value."""
    if "/" not in value:
        raise ScaffoldError(f"{flag} {value!r} must be '<source>/<resource>'")
    source, _, resource = value.partition("/")
    if not source or not resource or "/" in resource:
        raise ScaffoldError(f"{flag} {value!r} must be '<source>/<resource>'")
    return source, resource
