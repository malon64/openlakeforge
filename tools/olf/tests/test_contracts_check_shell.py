"""Shell-orchestration coverage for `scripts/local/stack/deploy-artifacts.sh`.

Fakes go at the `uv` boundary -- `scripts/lib/python.sh`'s `olf_run()` is
literally `uv run --project "${OLF_PROJECT_DIR}" --quiet olf "$@"`, so faking
`uv` is the real integration seam the script calls through. This runs the
real script under a faked PATH and asserts on its actual invocation order,
replacing check-contracts.sh's `.find()`-index string-order hack on script
*source* with running the script and observing real order.

Per AGENTS.md rule 4 ("Python for behaviour, shell for structure"), this
coverage lives in pytest, not inside `olf contracts check` itself -- matching
`release.py`'s precedent of never spawning shell subprocesses from a CLI
check.
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).parent / "fixtures"

_FAKE_UV = """#!/usr/bin/env bash
set -euo pipefail
echo "uv $*" >> "${CALL_LOG}"
# Strip "run --project <dir> --quiet olf" to get the forwarded subcommand.
shift 5
case "$*" in
  "contracts env"*)
    cat <<'EXPORTS'
export OPENLAKEFORGE_STORAGE_IMPLEMENTATION="storage.s3_compatible.seaweedfs"
export OPENLAKEFORGE_GOVERNANCE_ENABLED="false"
export OPENLAKEFORGE_CATALOG_TYPE="rest"
export OPENLAKEFORGE_CATALOG_PROVIDER="polaris"
EXPORTS
    ;;
  "floe render-profile"*)
    echo "apiVersion: floe/v1"
    ;;
  "revision activate"*)
    echo "fake-revision-1"
    ;;
  *)
    ;;
esac
"""

_FAKE_KUBECTL = """#!/usr/bin/env bash
set -euo pipefail
echo "kubectl $*" >> "${CALL_LOG}"
if [[ "$1" == "config" && "$2" == "get-contexts" ]]; then
  echo "${KUBE_CONTEXT}"
  exit 0
fi
exit 0
"""

_FAKE_DOCKER = """#!/usr/bin/env bash
set -euo pipefail
echo "docker $*" >> "${CALL_LOG}"
exit 0
"""


def _write_fake_bin(bin_dir: Path, name: str, script_body: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / name
    stub.write_text(script_body)
    stub.chmod(0o755)


def _run_deploy_artifacts(tmp_path: Path, script_path: Path) -> list[str]:
    bin_dir = tmp_path / "bin"
    _write_fake_bin(bin_dir, "uv", _FAKE_UV)
    _write_fake_bin(bin_dir, "kubectl", _FAKE_KUBECTL)
    _write_fake_bin(bin_dir, "docker", _FAKE_DOCKER)

    log_path = tmp_path / "calls.log"
    log_path.write_text("")

    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "CALL_LOG": str(log_path),
        "NAMESPACE": "lakehouse",
        "CLUSTER_NAME": "openlakeforge-test",
        "KUBE_CONTEXT": "kind-openlakeforge-test",
        "KUBECONFIG_PATH": str(tmp_path / "kubeconfig.yaml"),
        "PROJECT_CODE_IMAGE_TAG": "test-tag",
        "FLOE_RUNTIME_ARTIFACT_DIR": str(tmp_path / "floe-runtime"),
        "FLOE_PERSIST_RUNTIME_ARTIFACTS": "true",
        "OPENLAKEFORGE_REPO_ROOT": str(ROOT),
        "HOME": str(tmp_path / "home"),
    }
    (tmp_path / "home").mkdir(exist_ok=True)

    result = subprocess.run(
        ["bash", str(script_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"script failed:\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    return log_path.read_text().splitlines()


def test_local_deploy_artifacts_reconciles_namespaces_before_image_set(tmp_path: Path) -> None:
    calls = _run_deploy_artifacts(tmp_path, ROOT / "scripts/local/stack/deploy-artifacts.sh")

    sync_index = next(i for i, line in enumerate(calls) if "catalog sync-namespaces" in line)
    image_set_index = next(i for i, line in enumerate(calls) if "k8s set-project-code-image" in line)
    assert sync_index < image_set_index, calls


def test_local_deploy_artifacts_does_not_reintroduce_rollout_restart(tmp_path: Path) -> None:
    calls = _run_deploy_artifacts(tmp_path, ROOT / "scripts/local/stack/deploy-artifacts.sh")

    assert not any("rollout restart" in line for line in calls), calls


def test_wrong_order_fixture_is_actually_caught(tmp_path: Path) -> None:
    """Proves the assertions above are load-bearing: a deliberately-broken
    script fails both checks, rather than the tests trivially passing
    against any script."""
    calls = _run_deploy_artifacts(tmp_path, FIXTURES / "shell/deploy-artifacts-wrong-order.sh")

    sync_index = next(i for i, line in enumerate(calls) if "catalog sync-namespaces" in line)
    image_set_index = next(i for i, line in enumerate(calls) if "k8s set-project-code-image" in line)
    assert sync_index > image_set_index
    assert any("rollout restart" in line for line in calls)
