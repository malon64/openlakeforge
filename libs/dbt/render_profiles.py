from __future__ import annotations

import argparse
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_TEMPLATE_DIR = Path(__file__).resolve().parent / "profiles"
DEFAULT_RUNTIME_PROFILE_ROOT = Path(tempfile.gettempdir()) / "openlakeforge-dbt-profiles"

ENVIRONMENT_ALIASES = {
    "local": "local",
    "local-k8s": "local",
    "kind": "local",
    "azure": "azure",
    "azure-aks": "azure",
    "aks": "azure",
    "aws": "aws",
    "aws-eks": "aws",
    "eks": "aws",
}


def infer_environment(env: Mapping[str, str] | None = None) -> str:
    values = env or os.environ
    explicit = values.get("OPENLAKEFORGE_DBT_PROFILE_ENV", "").strip()
    if explicit:
        return normalize_environment(explicit)

    catalog_type = values.get("OPENLAKEFORGE_CATALOG_TYPE", "").strip().lower()
    catalog_provider = values.get("OPENLAKEFORGE_CATALOG_PROVIDER", "").strip().lower()
    if catalog_type == "glue" and catalog_provider == "aws-glue":
        return "aws"

    storage_provider = values.get("OPENLAKEFORGE_STORAGE_PROVIDER", "").strip().lower()
    if storage_provider == "azure":
        return "azure"

    return "local"


def normalize_environment(environment: str) -> str:
    key = environment.strip().lower()
    try:
        return ENVIRONMENT_ALIASES[key]
    except KeyError as exc:
        supported = ", ".join(sorted(ENVIRONMENT_ALIASES))
        raise ValueError(f"Unsupported dbt profile environment {environment!r}. Supported: {supported}.") from exc


def discover_project_dirs(root: Path | None = None) -> list[Path]:
    search_root = root or REPO_ROOT / "domains"
    return sorted(path.parent for path in search_root.glob("*/transformations/dbt/*/dbt_project.yml"))


def render_profile(project_dir: Path, environment: str | None = None, env: Mapping[str, str] | None = None) -> str:
    project_dir = project_dir.resolve()
    profile_environment = normalize_environment(environment) if environment else infer_environment(env)
    template_path = PROFILE_TEMPLATE_DIR / f"{profile_environment}.yml"
    if not template_path.exists():
        raise FileNotFoundError(f"Missing dbt profile template: {template_path}")

    profile_name = _read_dbt_project_field(project_dir / "dbt_project.yml", "profile")
    if not profile_name:
        profile_name = _read_dbt_project_field(project_dir / "dbt_project.yml", "name")
    if not profile_name:
        raise ValueError(f"Cannot determine dbt profile name from {project_dir / 'dbt_project.yml'}")

    domain = _domain_name(project_dir)
    product = project_dir.name
    replacements = {
        "{{PROFILE_NAME}}": profile_name,
        "{{DEFAULT_DUCKDB_PATH}}": f"/tmp/openlakeforge-{profile_name.replace('_', '-')}-dbt.duckdb",
        "{{GOLD_SCHEMA}}": f"{domain}_{product}_gold",
    }

    rendered = template_path.read_text(encoding="utf-8")
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


def write_profile(
    project_dir: Path,
    environment: str | None = None,
    output_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    project_dir = project_dir.resolve()
    target_dir = output_dir.resolve() if output_dir else project_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    profile_path = target_dir / "profiles.yml"
    profile_path.write_text(render_profile(project_dir, environment=environment, env=env), encoding="utf-8")
    return profile_path


def ensure_runtime_profile_dir(
    project_dir: Path,
    environment: str | None = None,
    root: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    project_dir = project_dir.resolve()
    profile_name = _read_dbt_project_field(project_dir / "dbt_project.yml", "profile") or project_dir.name
    target_dir = (root or DEFAULT_RUNTIME_PROFILE_ROOT) / profile_name
    write_profile(project_dir, environment=environment, output_dir=target_dir, env=env)
    return target_dir


def _read_dbt_project_field(path: Path, field: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(field)}\s*:\s*[\"']?([^\"'#]+)[\"']?\s*(?:#.*)?$")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.match(line)
        if match:
            return match.group(1).strip()
    return None


def _domain_name(project_dir: Path) -> str:
    parts = project_dir.parts
    try:
        return parts[parts.index("domains") + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Cannot derive domain name from dbt project path: {project_dir}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Render environment-specific OpenLakeForge dbt profiles.")
    parser.add_argument("--environment", default=None)
    parser.add_argument("--project-dir", action="append", type=Path, default=[])
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--write", action="store_true", help="Write profiles.yml instead of printing the first profile.")
    args = parser.parse_args()

    project_dirs = [path.resolve() for path in args.project_dir] if args.project_dir else discover_project_dirs()
    if not project_dirs:
        raise SystemExit("ERROR: no product dbt projects found.")

    if args.write:
        for project_dir in project_dirs:
            target_dir = args.output_dir if args.output_dir and len(project_dirs) == 1 else None
            profile_path = write_profile(project_dir, environment=args.environment, output_dir=target_dir)
            print(f"Rendered {profile_path}")
        return

    print(render_profile(project_dirs[0], environment=args.environment), end="")


if __name__ == "__main__":
    main()
