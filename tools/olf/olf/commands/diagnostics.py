"""Best-effort diagnostics collection using structured subprocess argv."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from pathlib import Path

import typer

from olf import config
from olf.deployment.engine import Toolkit

app = typer.Typer(help="Collect bounded deployment diagnostics.")
_OUTPUT_ARGUMENT = typer.Argument(..., help="Directory to receive text diagnostics.")


def _capture(tools: Toolkit, output: Path, name: str, argv_factory: Callable[[], list[str]]) -> str:
    """Capture one diagnostic command, including command-resolution failures."""
    try:
        argv = argv_factory()
        result = tools.runner.run(argv, check=False)
        text = result.stdout + result.stderr
    except Exception as exc:  # Diagnostics must preserve partial evidence.
        text = f"{type(exc).__name__}: {exc}\n"
    (output / name).write_text(text)
    return text


@app.command("tree")
def tree() -> None:
    """Print the checkout tree without delegating to a shell utility."""
    root = config.repo_root()
    for path in sorted(root.rglob("*")):
        if ".git" not in path.parts:
            typer.echo(str(path.relative_to(root)))


@app.command("collect")
def collect(
    output_dir: Path = _OUTPUT_ARGUMENT,
    namespace: str = typer.Option("", "--namespace"),
    kube_context: str = typer.Option("", "--kube-context"),
) -> None:
    """Collect host, Docker, node, workload, event, and bounded pod-log evidence."""
    output_dir.mkdir(parents=True, exist_ok=True)
    selected_namespace = namespace or config.namespace()
    selected_context = kube_context or os.environ.get("KUBE_CONTEXT", "")
    disk = shutil.disk_usage(output_dir)
    (output_dir / "host-disk.txt").write_text(f"total={disk.total}\nused={disk.used}\nfree={disk.free}\n")
    tools = Toolkit.default()
    context = ["--context", selected_context] if selected_context else []
    def command(tool: str, *args: str) -> list[str]:
        return [str(tools.resolver.resolve(tool)), *args]

    _capture(tools, output_dir, "docker-disk.txt", lambda: command("docker", "system", "df"))
    _capture(tools, output_dir, "nodes.txt", lambda: command("kubectl", *context, "get", "nodes", "-o", "wide"))
    _capture(tools, output_dir, "all-pods.txt", lambda: command("kubectl", *context, "get", "pods", "-A", "-o", "wide"))
    _capture(
        tools,
        output_dir,
        "all-events.txt",
        lambda: command("kubectl", *context, "get", "events", "-A", "--sort-by=.lastTimestamp"),
    )
    _capture(
        tools,
        output_dir,
        "workloads.txt",
        lambda: command("kubectl", *context, "get", "all", "-n", selected_namespace, "-o", "wide"),
    )
    _capture(
        tools,
        output_dir,
        "events.txt",
        lambda: command("kubectl", *context, "get", "events", "-n", selected_namespace, "--sort-by=.lastTimestamp"),
    )
    _capture(
        tools,
        output_dir,
        "pod-descriptions.txt",
        lambda: command("kubectl", *context, "describe", "pods", "-n", selected_namespace),
    )
    pods = _capture(
        tools,
        output_dir,
        "pod-names.txt",
        lambda: command("kubectl", *context, "get", "pods", "-n", selected_namespace, "-o", "name"),
    )
    for item in pods.splitlines():
        if item.startswith("pod/"):
            pod = item.removeprefix("pod/")
            _capture(
                tools,
                output_dir,
                f"{pod}.log",
                lambda pod=pod: command(
                    "kubectl", *context, "logs", "-n", selected_namespace, pod, "--all-containers", "--tail=200"
                ),
            )
    typer.echo(f"Diagnostics written to {output_dir}")
