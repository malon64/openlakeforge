"""Superset report bundle build, import, and export.

Replaces scripts/local/artifacts/superset-reports-deploy.sh and
superset-reports-export.sh. Bundle building and unpacking are pure local
operations; the import and export commands run inside the Superset pod (they
need Superset's own interpreter and database), so those bodies stay as scripts
executed through `kubectl exec`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

from olf import k8s, log

REPORTS_MOUNT_PATH_DEFAULT = "/app/openlakeforge/reports"

# In-pod importer. Runs in the Superset interpreter; argv: <remote_bundle> <username>.
_IMPORT_SCRIPT = """
import sys
from zipfile import ZipFile

from flask import g

from superset.app import create_app

bundle_path = sys.argv[1]
username = sys.argv[2]

app = create_app()
with app.app_context():
    from superset import security_manager
    from superset.commands.importers.v1.assets import ImportAssetsCommand
    from superset.commands.importers.v1.utils import get_contents_from_bundle

    user = security_manager.find_user(username=username)
    if user is None:
        raise SystemExit(f"Superset user '{username}' does not exist")

    g.user = user
    with ZipFile(bundle_path) as bundle:
        contents = get_contents_from_bundle(bundle)
    ImportAssetsCommand(contents).run()
"""

# In-pod exporter. argv: <remote_bundle> <username> <dashboard_title> <bundle_root>.
_EXPORT_SCRIPT = """
import sys
from zipfile import ZIP_DEFLATED, ZipFile

import yaml
from flask import g

from superset.app import create_app

bundle_path = sys.argv[1]
username = sys.argv[2]
dashboard_title = sys.argv[3]
bundle_root = sys.argv[4]

app = create_app()
with app.app_context():
    from superset import security_manager
    from superset.commands.dashboard.export import ExportDashboardsCommand
    from superset.extensions import db
    from superset.models.dashboard import Dashboard

    user = security_manager.find_user(username=username)
    if user is None:
        raise SystemExit(f"Superset user '{username}' does not exist")
    g.user = user

    dashboard_ids = [
        dashboard_id
        for (dashboard_id,) in db.session.query(Dashboard.id)
        .filter(Dashboard.dashboard_title == dashboard_title)
        .all()
    ]
    if not dashboard_ids:
        raise SystemExit(f"Superset dashboard '{dashboard_title}' does not exist")

    with ZipFile(bundle_path, "w", ZIP_DEFLATED) as bundle:
        for file_name, file_content in ExportDashboardsCommand(dashboard_ids).run():
            content = file_content()
            if file_name == "metadata.yaml":
                metadata = yaml.safe_load(content)
                metadata["type"] = "assets"
                content = yaml.safe_dump(metadata, sort_keys=False)
            with bundle.open(f"{bundle_root}/{file_name}", "w") as fp:
                fp.write(content.encode())
"""


@dataclass(frozen=True)
class ReportBundle:
    root: str
    name: str


def bundle_identity(report_source_dir: str) -> ReportBundle:
    """Derive the bundle root/name from the report source directory path."""
    root = re.sub(r"^domains/", "", report_source_dir)
    root = root.replace("/reports/superset/", "_").replace("/", "_")
    root = f"{root}_superset_bundle"
    return ReportBundle(root=root, name=f"{root}.zip")


def build_report_bundle(source_dir: Path, bundle_path: Path, bundle_root: str, sqlalchemy_uri: str) -> None:
    """Zip the report YAML, rewriting the database sqlalchemy_uri in place."""
    with ZipFile(bundle_path, "w", ZIP_DEFLATED) as bundle:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
                continue
            relative = path.relative_to(source_dir).as_posix()
            archive_name = PurePosixPath(bundle_root, relative).as_posix()
            if relative.startswith("databases/"):
                text = path.read_text(encoding="utf-8")
                text = re.sub(
                    r"^sqlalchemy_uri:\s*.+$",
                    f"sqlalchemy_uri: {sqlalchemy_uri}",
                    text,
                    flags=re.MULTILINE,
                )
                bundle.writestr(archive_name, text)
            else:
                bundle.write(path, archive_name)


def unpack_export_bundle(bundle_path: Path, target_dir: Path) -> None:
    """Replace managed report assets in the source tree from an export zip."""
    managed_names = {"metadata.yaml", "databases", "datasets", "charts", "dashboards"}
    target_dir.mkdir(parents=True, exist_ok=True)
    for name in managed_names:
        path = target_dir / name
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()

    with ZipFile(bundle_path) as bundle:
        for member in bundle.namelist():
            path = PurePosixPath(member)
            if len(path.parts) < 2 or path.name.startswith(".") or path.suffix.lower() not in {".yaml", ".yml"}:
                continue
            relative = PurePosixPath(*path.parts[1:])
            destination = target_dir / Path(relative.as_posix())
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(bundle.read(member))


def _running_superset_pod(namespace: str) -> str:
    raw = k8s._kubectl(  # noqa: SLF001 - internal helper reuse
        [
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            "app=superset,release=superset",
            "-o",
            'jsonpath={range .items[?(@.status.phase=="Running")]}{.metadata.name}{"\\n"}{end}',
        ],
        capture=True,
    )
    pod = next((line for line in raw.splitlines() if line), "")
    if not pod:
        raise RuntimeError("could not find a running Superset web pod.")
    return pod


def discover_report_dirs(repo_root: Path) -> list[str]:
    root = repo_root / "domains"
    dirs = {
        str(path.parent.relative_to(repo_root))
        for path in root.glob("*/reports/superset/*/metadata.yaml")
    }
    return sorted(dirs)


def _exec_pod_python(pod: str, namespace: str, script: str, args: list[str]) -> None:
    quoted = " ".join(f"'{arg}'" for arg in args)
    command = f". /app/pythonpath/superset_bootstrap.sh; python - {quoted}"
    subprocess.run(
        ["kubectl", "exec", "-i", pod, "-c", "superset", "-n", namespace, "--", "/bin/sh", "-ec", command],
        input=script,
        text=True,
        check=True,
    )


def deploy_reports(
    repo_root: Path,
    namespace: str,
    sqlalchemy_uri: str,
    *,
    report_source_dir: str | None,
    work_dir: Path,
    reports_mount_path: str,
    admin_username: str,
) -> None:
    log.step("Waiting for Superset web deployment...")
    k8s.wait_for_rollout("deployment/superset", namespace)
    pod = _running_superset_pod(namespace)

    report_dirs = [report_source_dir] if report_source_dir else discover_report_dirs(repo_root)
    if not report_dirs:
        raise RuntimeError("no product Superset report assets found.")

    work_dir.mkdir(parents=True, exist_ok=True)
    for report_dir in report_dirs:
        source_dir = repo_root / report_dir
        if not (source_dir / "metadata.yaml").is_file():
            raise RuntimeError(f"missing Superset report metadata at {report_dir}/metadata.yaml")

        identity = bundle_identity(report_dir)
        bundle_path = work_dir / identity.name
        remote_bundle = f"{reports_mount_path}/{identity.name}"
        build_report_bundle(source_dir, bundle_path, identity.root, sqlalchemy_uri)

        log.step(f"Copying {bundle_path} to {pod}:{remote_bundle}")
        with bundle_path.open("rb") as body:
            subprocess.run(
                [
                    "kubectl", "exec", "-i", pod, "-c", "superset", "-n", namespace, "--",
                    "/bin/sh", "-ec", f"mkdir -p '{reports_mount_path}' && cat > '{remote_bundle}'",
                ],
                stdin=body,
                check=True,
            )

        log.step(f"Importing Superset report assets from {remote_bundle}")
        _exec_pod_python(pod, namespace, _IMPORT_SCRIPT, [remote_bundle, admin_username])
        log.info(f"Deployed Superset report assets from {report_dir}")


def export_report(
    repo_root: Path,
    namespace: str,
    *,
    report_source_dir: str,
    bundle_name: str,
    work_dir: Path,
    reports_mount_path: str,
    admin_username: str,
    dashboard_title: str,
) -> None:
    identity = bundle_identity(report_source_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    local_bundle = work_dir / bundle_name
    remote_bundle = f"{reports_mount_path}/{bundle_name}"

    log.step("Waiting for Superset web deployment...")
    k8s.wait_for_rollout("deployment/superset", namespace)
    pod = _running_superset_pod(namespace)

    log.step(f"Exporting '{dashboard_title}' from Superset")
    _exec_pod_python(
        pod, namespace, _EXPORT_SCRIPT, [remote_bundle, admin_username, dashboard_title, identity.root]
    )

    with local_bundle.open("wb") as out:
        subprocess.run(
            ["kubectl", "exec", pod, "-c", "superset", "-n", namespace, "--", "cat", remote_bundle],
            stdout=out,
            check=True,
        )

    unpack_export_bundle(local_bundle, repo_root / report_source_dir)
    log.info(f"Exported Superset report assets to {report_source_dir}")
