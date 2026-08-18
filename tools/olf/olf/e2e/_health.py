"""Pod and bootstrap-Job health classification and bounded diagnostics."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any

from olf import log
from olf.e2e._shell import E2EConfig, E2EError, kubectl

READINESS_ATTEMPTS = 60
READINESS_DELAY_SECONDS = 5
DIAGNOSTIC_LOG_LINES = 80
REQUIRED_READINESS_LABEL = "required"


def _bounded_pod_diagnostics(cfg: E2EConfig, names: list[str]) -> str:
    lines: list[str] = []
    for name in names[:10]:
        try:
            output = kubectl(
                cfg,
                ["logs", "-n", cfg.namespace, f"pod/{name}", "--all-containers", f"--tail={DIAGNOSTIC_LOG_LINES}"],
                capture=True,
            )
        except E2EError as exc:
            output = f"unable to collect logs: {exc}"
            try:
                description = kubectl(cfg, ["describe", "pod", "-n", cfg.namespace, name], capture=True)
            except E2EError as describe_exc:
                output += f"\nunable to describe pod: {describe_exc}"
            else:
                output += f"\npod description:\n{description[-6000:]}"
        lines.append(f"{name}:\n{output[-6000:]}")
    return "\n".join(lines)


def _bounded_job_diagnostics(cfg: E2EConfig, names: list[str]) -> str:
    """Collect only a small tail from the named Jobs."""
    diagnostics: list[str] = []
    for job_name in names[:10]:
        try:
            output = kubectl(
                cfg,
                ["logs", "-n", cfg.namespace, f"job/{job_name}", f"--tail={DIAGNOSTIC_LOG_LINES}"],
                capture=True,
            )
        except E2EError as exc:
            output = f"unable to collect logs: {exc}"
        diagnostics.append(f"{job_name}:\n{output[-6000:]}")
    return "\n".join(diagnostics)


def check_pods_ready(cfg: E2EConfig) -> None:
    log.step("Checking pod health...")
    service_bad: list[str] = []
    job_bad: list[str] = []
    last_error: E2EError | None = None
    reported_warnings: set[str] = set()
    for _ in range(READINESS_ATTEMPTS):
        try:
            pod_payload = json.loads(kubectl(cfg, ["get", "pods", "-n", cfg.namespace, "-o", "json"], capture=True))
            job_payload = json.loads(kubectl(cfg, ["get", "jobs", "-n", cfg.namespace, "-o", "json"], capture=True))
        except E2EError as exc:
            last_error = exc
            time.sleep(5)
            continue
        service_bad = classify_pod_health(pod_payload)
        job_bad, warned = classify_job_health(job_payload, suite_jobs=cfg.inventory.job_names)
        new_warnings = [message for message in warned if message not in reported_warnings]
        reported_warnings.update(new_warnings)
        for message in new_warnings:
            log.warn(message)
        if new_warnings:
            warning_job_names = [message.split(":", 1)[0] for message in new_warnings]
            diagnostics = _bounded_job_diagnostics(cfg, warning_job_names)
            if diagnostics:
                log.warn(f"non-blocking Job diagnostics:\n{diagnostics}")
        if not service_bad and not job_bad:
            return
        time.sleep(READINESS_DELAY_SECONDS)
    if last_error is not None and not service_bad and not job_bad:
        raise last_error
    diagnostics: list[str] = []
    if service_bad:
        diagnostics.append(_bounded_pod_diagnostics(cfg, [message.split(":", 1)[0] for message in service_bad]))
    if job_bad:
        diagnostics.append(_bounded_job_diagnostics(cfg, [message.split(":", 1)[0] for message in job_bad]))
    raise E2EError(
        "unhealthy required services:\n"
        + "\n".join([*service_bad, *job_bad])
        + "\nDiagnostics:\n"
        + "\n".join(diagnostics)
    )


def classify_pod_health(payload: Mapping[str, Any]) -> list[str]:
    """Return blocking health failures for long-lived service pods only."""
    bad: list[str] = []
    for item in payload.get("items", []):
        owner_kinds = {str(owner.get("kind")) for owner in item.get("metadata", {}).get("ownerReferences", [])}
        if "Job" not in owner_kinds:
            bad.extend(unhealthy_pod_messages({"items": [item]}))
    return bad


def classify_job_health(
    payload: Mapping[str, Any], *, suite_jobs: Sequence[str] = ()
) -> tuple[list[str], list[str]]:
    """Return blocking bootstrap Job failures and warnings for non-blocking Jobs."""
    bad: list[str] = []
    warned: list[str] = []
    for item in payload.get("items", []):
        metadata = item.get("metadata", {})
        name = str(metadata.get("name", "<unnamed>"))
        labels = item.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {})
        state = job_state(item)
        if labels.get("openlakeforge.io/readiness") == REQUIRED_READINESS_LABEL:
            if state != "Complete":
                bad.append(f"{name}: {state}")
            continue
        if is_active_suite_job(item, suite_jobs, state):
            bad.append(f"{name}: {state}")
            continue
        if state != "Complete":
            warned.append(f"{name}: non-blocking Job is {state}; continuing E2E readiness")
    return bad, warned


def is_active_suite_job(item: Mapping[str, Any], suite_jobs: Sequence[str], state: str) -> bool:
    """Whether a currently active Dagster Job belongs to this E2E suite."""
    if state not in {"Pending", "Running"}:
        return False
    metadata = item.get("metadata", {})
    labels = metadata.get("labels", {})
    identities = {
        str(metadata.get("name", "")),
        str(labels.get("dagster/job", "")),
        str(labels.get("job-name", "")),
        *(str(owner.get("name", "")) for owner in metadata.get("ownerReferences", [])),
    }
    return any(job_name in identities for job_name in suite_jobs)


def job_state(item: Mapping[str, Any]) -> str:
    """Return the terminal or current state represented by a Kubernetes Job."""
    conditions = item.get("status", {}).get("conditions", [])
    completed = any(
        condition.get("type") == "Complete" and condition.get("status") == "True" for condition in conditions
    )
    if completed:
        return "Complete"
    failed = any(
        condition.get("type") == "Failed" and condition.get("status") == "True" for condition in conditions
    )
    if failed:
        return "Failed"
    if item.get("status", {}).get("active"):
        return "Running"
    return "Pending"


def unhealthy_pod_messages(payload: Mapping[str, Any]) -> list[str]:
    bad = []
    for item in payload.get("items", []):
        name = item["metadata"]["name"]
        phase = item.get("status", {}).get("phase")
        if phase == "Succeeded":
            continue
        if phase == "Running":
            statuses = item.get("status", {}).get("containerStatuses", [])
            unready = [status["name"] for status in statuses if not status.get("ready")]
            if not unready:
                continue
            bad.append(f"{name}: Running but containers not ready: {', '.join(unready)}")
            continue
        bad.append(f"{name}: {phase}")
    return bad
