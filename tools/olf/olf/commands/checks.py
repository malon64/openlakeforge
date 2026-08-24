"""Repository validation commands replacing ``scripts/test/*.sh``."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import sys
import tempfile
import tomllib
from pathlib import Path

import typer
import yaml

from olf import config
from olf.commands._shared import fail
from olf.deployment.engine import Toolkit
from olf.deployment.errors import DeploymentError

app = typer.Typer(help="Repository validation and contributor checks.")


def _root(repo_root: str) -> Path:
    return Path(repo_root or config.repo_root()).resolve()


def _run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> None:
    try:
        Toolkit.default().runner.run(argv, cwd=cwd, env=env, stream_output=True)
    except DeploymentError as exc:
        raise typer.Exit(code=fail(str(exc))) from exc


def _uv_pip_install(*, target: Path, requirements: list[str], cwd: Path) -> None:
    """Install a check-only dependency set without requiring pip in uv's venv."""
    uv = str(Toolkit.default().resolver.resolve("uv"))
    _run(
        [
            uv,
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(target),
            "--no-compile",
            *requirements,
        ],
        cwd=cwd,
    )


@app.command("structure")
def structure(repo_root: str = typer.Option("", "--repo-root", help="Checkout root to validate.")) -> None:
    """Validate the essential repository skeleton and prohibit shell scripts."""
    from olf.dashboard_checks import validate_superset_assets

    root = _root(repo_root)
    required = (
        "README.md",
        "AGENTS.md",
        "Makefile",
        "docs/adr/README.md",
        "lakehouse_code/lakehouse.yaml",
        "infra/terraform/environments/local/contracts.tf",
        "tools/olf/pyproject.toml",
    )
    missing = [path for path in required if not (root / path).exists()]
    modules_root = root / "infra/terraform/modules"
    module_errors: list[str] = []
    if modules_root.is_dir():
        module_dirs = {path.parent for path in modules_root.rglob("*.tf")}
        for module in sorted(module_dirs):
            for filename in ("main.tf", "variables.tf", "outputs.tf"):
                if not (module / filename).is_file():
                    module_errors.append(f"Terraform module {module.relative_to(root)} is missing {filename}")
    tracked = Toolkit.default().runner.run(["git", "ls-files", "-z"], cwd=root).stdout.split("\0")
    scripts = [root / path for path in tracked if path.endswith(".sh") and (root / path).is_file()]
    if missing or scripts or module_errors:
        details = [
            *(f"missing required path: {path}" for path in missing),
            *module_errors,
            *(f"shell script is forbidden: {path.relative_to(root)}" for path in scripts),
        ]
        raise typer.Exit(code=fail("\n".join(details)))
    dashboard_errors = validate_superset_assets(root)
    if dashboard_errors:
        raise typer.Exit(code=fail("\n".join(dashboard_errors)))
    typer.echo("Repository structure is valid.")


@app.command("contracts")
def contracts(repo_root: str = typer.Option("", "--repo-root", help="Checkout root to validate.")) -> None:
    """Run the existing parsed provider-contract validation."""
    from olf import contracts_check

    report = contracts_check.run_contracts_check(_root(repo_root))
    typer.echo(report.render())
    if not report.ok:
        raise typer.Exit(code=1)


@app.command("components")
def components(repo_root: str = typer.Option("", "--repo-root", help="Checkout root to validate.")) -> None:
    """Validate component catalog pins and release-readiness inputs."""
    from olf import release

    root = _root(repo_root)
    report = release.run_release_check(root)
    typer.echo(report.render())
    if not report.ok:
        raise typer.Exit(code=1)


@app.command("infra")
def infra(repo_root: str = typer.Option("", "--repo-root", help="Checkout root to validate.")) -> None:
    """Format/validate Terraform roots and render pinned local Helm charts."""
    root = _root(repo_root)
    tools = Toolkit.default()
    env = {
        "HELM_REPOSITORY_CONFIG": str(root / ".tmp/check-helm/repositories.yaml"),
        "HELM_REPOSITORY_CACHE": str(root / ".tmp/check-helm/cache"),
    }
    roots = (
        "infra/terraform/foundations/local-kind",
        "infra/terraform/foundations/azure-aks",
        "infra/terraform/foundations/aws-eks",
        "infra/terraform/environments/local",
        "infra/terraform/environments/azure-poc",
        "infra/terraform/environments/aws-poc",
    )
    terraform = str(tools.resolver.resolve("terraform"))
    tools.runner.run([terraform, "fmt", "-check", "-recursive", str(root / "infra/terraform")], stream_output=True)
    for relative in roots:
        directory = root / relative
        tools.terraform.init(
            directory,
            extra_args=("-backend=false", "-input=false", "-lockfile=readonly"),
            env=env,
        )
        tools.runner.run([terraform, f"-chdir={directory}", "validate"], env=env, stream_output=True)
    charts = (
        ("seaweedfs", "seaweedfs/seaweedfs", "4.23.0", "infra/helm/values/local/seaweedfs.yaml"),
        ("polaris", "polaris/polaris", "1.4.1", "infra/helm/values/local/polaris.yaml"),
        ("trino", "trino/trino", "1.42.2", "infra/helm/values/local/trino.yaml"),
        ("dagster", "dagster/dagster", "1.13.6", "infra/helm/values/local/dagster.yaml"),
        ("superset", "superset/superset", "0.15.5", "infra/helm/values/local/superset.yaml"),
    )
    repos = (
        ("seaweedfs", "https://seaweedfs.github.io/seaweedfs/helm"),
        ("polaris", "https://downloads.apache.org/polaris/helm-chart"),
        ("trino", "https://trinodb.github.io/charts"),
        ("dagster", "https://dagster-io.github.io/helm"),
        ("superset", "http://apache.github.io/superset/"),
    )
    for name, url in repos:
        tools.helm.repo_add(name, url, env=env)
    tools.helm.repo_update(env=env)
    helm = str(tools.resolver.resolve("helm"))
    for release_name, chart, version, values in charts:
        args = [
            helm,
            "template",
            release_name,
            chart,
            "--version",
            version,
            "--namespace",
            "lakehouse",
            "--values",
            str(root / values),
        ]
        if release_name == "superset":
            args.extend(_superset_template_overrides())
        result = tools.runner.run(
            args,
            env=env,
        )
        if release_name == "polaris" and "polaris.persistence.type=relational-jdbc" not in result.stdout:
            raise typer.Exit(code=fail("rendered Polaris chart is not configured for relational JDBC persistence"))
        if release_name == "superset":
            _validate_superset_render(result.stdout)
    typer.echo("Infrastructure checks passed.")


def _superset_template_overrides() -> list[str]:
    """Mirror the Terraform Helm release inputs for local Superset rendering."""
    return [
        "--set",
        "image.repository=ghcr.io/openlakeforge/superset",
        "--set",
        "image.tag=local",
        "--set",
        "image.pullPolicy=Never",
        "--set",
        "extraSecretEnv.SUPERSET_SECRET_KEY=check",
        "--set",
        "supersetNode.connections.db_host=postgresql",
        "--set",
        "supersetNode.connections.db_port=5432",
        "--set",
        "supersetNode.connections.db_user=superset",
        "--set",
        "supersetNode.connections.db_pass=check",
        "--set",
        "supersetNode.connections.db_name=superset",
        "--set-json",
        'extraVolumes=[{"name":"superset-reports","emptyDir":{"sizeLimit":"1Gi"}}]',
        "--set-json",
        'extraVolumeMounts=[{"name":"superset-reports","mountPath":"/app/openlakeforge/reports"}]',
    ]


def _validate_superset_render(rendered: str) -> None:
    """Require the Terraform-owned ephemeral reports volume in rendered manifests."""
    documents = [item for item in yaml.safe_load_all(rendered) if isinstance(item, dict)]
    reports_volume = False
    reports_mount = False
    reports_pvc = False
    for document in documents:
        metadata = document.get("metadata", {})
        if document.get("kind") == "PersistentVolumeClaim" and metadata.get("name") == "superset-reports":
            reports_pvc = True
        pod_spec = document.get("spec", {}).get("template", {}).get("spec", {})
        for volume in pod_spec.get("volumes", []):
            if volume.get("name") == "superset-reports" and "emptyDir" in volume:
                reports_volume = True
        for container in pod_spec.get("containers", []):
            for mount in container.get("volumeMounts", []):
                if mount.get("name") == "superset-reports" and mount.get("mountPath") == "/app/openlakeforge/reports":
                    reports_mount = True
    if not reports_volume or not reports_mount or reports_pvc:
        raise typer.Exit(
            code=fail(
                "rendered Superset chart must include the superset-reports emptyDir and mount, without a reports PVC"
            )
        )


@app.command("lockfiles")
def lockfiles(repo_root: str = typer.Option("", "--repo-root", help="Checkout root to validate.")) -> None:
    """Verify uv lockfiles declared by the component catalog are current."""
    root = _root(repo_root)
    catalog = yaml.safe_load((root / "release/component-catalog.yaml").read_text())
    locks = catalog["components"]["python"].values()
    uv = str(Toolkit.default().resolver.resolve("uv"))
    for lock in locks:
        path = root / lock
        if not path.is_file() or not path.stat().st_size:
            raise typer.Exit(code=fail(f"missing or empty lockfile: {path}"))
        if path.name == "uv.lock":
            _run([uv, "lock", "--project", str(path.parent), "--check"], cwd=root)
        else:
            _check_compiled_lock(path, uv=uv, root=root)
    typer.echo("Lockfiles are in sync.")


def _check_compiled_lock(path: Path, *, uv: str, root: Path) -> None:
    """Re-run the exact pinned compile command recorded in a requirements lock."""
    lines = path.read_text().splitlines()
    if len(lines) < 2 or not lines[1].lstrip("# ").startswith("uv pip compile "):
        raise typer.Exit(code=fail(f"{path}: line 2 must record a uv pip compile command"))
    argv = shlex.split(lines[1].lstrip("# "))
    try:
        index = argv.index("--output-file")
        original_output = argv[index + 1]
    except (ValueError, IndexError) as exc:
        raise typer.Exit(code=fail(f"{path}: compile command must include --output-file")) from exc
    if (root / original_output).resolve() != path.resolve():
        raise typer.Exit(code=fail(f"{path}: compile command output must be {path.relative_to(root)}"))
    argv[0] = uv
    with tempfile.TemporaryDirectory(prefix="olf-lockfile-") as temporary:
        compiled = Path(temporary) / path.name
        shutil.copy2(path, compiled)
        argv[index + 1] = str(compiled)
        _run([*argv, "--quiet"], cwd=root)
        if "\n".join(lines[2:]) != "\n".join(compiled.read_text().splitlines()[2:]):
            raise typer.Exit(
                code=fail(
                    f"{path.relative_to(root)} does not match its pinned uv pip compile command; regenerate it."
                )
            )


@app.command("project-code")
def project_code(repo_root: str = typer.Option("", "--repo-root", help="Checkout root to validate.")) -> None:
    """Load the merged Dagster definitions in the project-code dependency set."""
    root = _root(repo_root)
    if sys.version_info[:2] != (3, 12):
        raise typer.Exit(
            code=fail(
                "project-code check requires Python >=3.12,<3.13; "
                f"found {sys.version_info[0]}.{sys.version_info[1]}"
            )
        )
    cache = root / ".cache/project-code-check"
    project_digest = hashlib.sha256((root / "images/project-code/pyproject.toml").read_bytes()).hexdigest()[:16]
    domain_digest = _source_tree_digest(root / "packages/domain-model")
    site = cache / f"py{sys.version_info.major}{sys.version_info.minor}-{project_digest}-{domain_digest}" / "site"
    if not (site / ".complete").is_file():
        site.mkdir(parents=True, exist_ok=True)
        pyproject = tomllib.loads((root / "images/project-code/pyproject.toml").read_text())
        _uv_pip_install(target=site, requirements=pyproject["project"]["dependencies"], cwd=root)
        _uv_pip_install(target=site, requirements=[str(root / "packages/domain-model")], cwd=root)
        (site / ".complete").touch()
    env = {
        "PATH": f"{site / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        "PYTHONPATH": f"{site}{os.pathsep}{root}",
        "OPENLAKEFORGE_FLOE_MANIFEST_ACCESS_MODE": "remote",
        "OPENLAKEFORGE_OPS_BUCKET_NAME": "openlakeforge-ops",
        "OPENLAKEFORGE_ARTIFACT_BUCKET_NAME": "openlakeforge-ops",
        "OPENLAKEFORGE_ARTIFACT_BASE_URI": "s3://openlakeforge-ops/artifacts",
        "OPENLAKEFORGE_FLOE_MANIFEST_BASE_URI": "s3://openlakeforge-ops/floe/manifests",
        "OPENLAKEFORGE_FLOE_REPORT_BASE_URI": "s3://openlakeforge-ops/floe/reports",
        "OPENLAKEFORGE_LOG_BASE_URI": "s3://openlakeforge-ops/logs",
        "OPENLAKEFORGE_RUN_ARTIFACT_BASE_URI": "s3://openlakeforge-ops/runs",
    }
    _run([sys.executable, "-m", "olf.project_code_check", str(root)], cwd=root, env=env)


def _source_tree_digest(directory: Path) -> str:
    """Hash source paths and contents so editable project dependencies cannot go stale."""
    digest = hashlib.sha256()
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


@app.command("dbt")
def dbt(repo_root: str = typer.Option("", "--repo-root", help="Checkout root to validate.")) -> None:
    """Render, resolve, parse, and compile every discovered dbt product."""
    root = _root(repo_root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from libs.dbt.render_profiles import discover_project_dirs, write_profile

    projects = discover_project_dirs(root / "lakehouse_code/gold")
    if not projects:
        raise typer.Exit(code=fail("no product dbt projects found"))
    cache = root / ".cache/dbt-check"
    dependency_key = hashlib.sha256(b"dbt-trino==1.10.2\nopenlineage-dbt==1.45.0").hexdigest()[:16]
    site = cache / f"py{sys.version_info.major}{sys.version_info.minor}-{dependency_key}" / "site"
    if not (site / ".complete").is_file():
        site.mkdir(parents=True, exist_ok=True)
        _uv_pip_install(
            target=site,
            requirements=["dbt-trino==1.10.2", "openlineage-dbt==1.45.0"],
            cwd=root,
        )
        (site / ".complete").touch()
    dbt_bin = str(site / "bin/dbt")
    env = {
        "PATH": f"{site / 'bin'}{os.pathsep}{os.environ.get('PATH', '')}",
        "PYTHONPATH": f"{site}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
        "AWS_ACCESS_KEY_ID": "openlakeforge",
        "AWS_SECRET_ACCESS_KEY": "openlakeforge",
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ENDPOINT_URL_S3": "http://seaweedfs-s3:8333",
        "OPENLAKEFORGE_QUERY_TRINO_HOST": "trino",
        "OPENLAKEFORGE_QUERY_TRINO_PORT": "8080",
        "OPENLAKEFORGE_QUERY_TRINO_CATALOG": "iceberg",
        "OPENLAKEFORGE_CATALOG_NAME": "lakehouse_dev",
    }
    for project in projects:
        write_profile(project, environment="local")
        _run([dbt_bin, "deps", "--project-dir", str(project)], cwd=root, env=env)
        _run(
            [dbt_bin, "parse", "--project-dir", str(project), "--profiles-dir", str(project), "--target", "local"],
            cwd=root,
            env=env,
        )
        _run(
            [
                dbt_bin,
                "compile",
                "--project-dir",
                str(project),
                "--profiles-dir",
                str(project),
                "--target",
                "local",
                "--no-introspect",
                "--no-populate-cache",
            ],
            cwd=root,
            env=env,
        )
        _validate_dbt_relation_contract(project, root=root, catalog=env["OPENLAKEFORGE_CATALOG_NAME"])
    typer.echo("dbt projects compiled and relation contracts are valid.")


def _validate_dbt_relation_contract(project: Path, *, root: Path, catalog: str) -> None:
    """Assert compiled Gold models consume only their descriptor-owned Silver schema."""
    from openlakeforge_domain import load_lakehouse_inventory

    manifest_path = project / "target/manifest.json"
    if not manifest_path.is_file():
        raise typer.Exit(code=fail(f"dbt did not create {manifest_path}"))
    try:
        product = project.relative_to(root / "lakehouse_code/gold").parts[0]
    except (ValueError, IndexError) as exc:
        raise typer.Exit(code=fail(f"cannot derive product from dbt path: {project}")) from exc
    inventory = load_lakehouse_inventory(root / "lakehouse_code")
    descriptor = next((item for item in inventory.products if item.id == product), None)
    if descriptor is None:
        raise typer.Exit(code=fail(f"cannot resolve dbt product {product!r} from lakehouse.yaml"))
    manifest = json.loads(manifest_path.read_text())
    expected_gold = f"{product}_gold"
    expected_silver = f"{descriptor.domain_name}_silver"
    models = [
        f"{node.get('name')}: {node.get('database')}.{node.get('schema')}"
        for node in manifest.get("nodes", {}).values()
        if node.get("resource_type") == "model"
        and (node.get("database"), node.get("schema")) != (catalog, expected_gold)
    ]
    sources = [
        f"{source.get('name')}: {source.get('database')}.{source.get('schema')}"
        for source in manifest.get("sources", {}).values()
        if (source.get("database"), source.get("schema")) != (catalog, expected_silver)
    ]
    if models or sources:
        details = [
            *(f"Gold models must use {catalog}.{expected_gold}: {item}" for item in models),
            *(f"Silver sources must use {catalog}.{expected_silver}: {item}" for item in sources),
        ]
        raise typer.Exit(code=fail("\n".join(details)))


@app.command("all")
def all_checks(repo_root: str = typer.Option("", "--repo-root", help="Checkout root to validate.")) -> None:
    """Run the complete contributor and release-readiness gate."""
    structure(repo_root)
    components(repo_root)
    contracts(repo_root)
    infra(repo_root)
    project_code(repo_root)
    dbt(repo_root)
    lockfiles(repo_root)
