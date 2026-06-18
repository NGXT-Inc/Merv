"""Live Lambda smoke for Notebook Lens over SSH.

This is intentionally a one-off probe. It provisions a Lambda Labs VM through
the Research Plugin backend, rsyncs the standalone notebook-lens repo to the VM,
runs the CLI remotely over SSH, and releases the VM in a finally block.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "research_plugin"
NOTEBOOK_LENS = ROOT.parent / "notebook-lens"
RUN_DIR = ROOT / "tmp" / "notebook_lens_lambda_probe"

sys.path.insert(0, str(PLUGIN_ROOT))

os.environ.setdefault("RESEARCH_PLUGIN_LAMBDA_ENV_FILE", str(PLUGIN_ROOT / ".env"))
os.environ.setdefault("RESEARCH_PLUGIN_LAMBDA_WORKDIR", "/home/ubuntu/rp_synced")
os.environ.setdefault("RESEARCH_PLUGIN_LAMBDA_DATA_DIR", "/home/ubuntu/rp_unsynced")
os.environ.setdefault("RESEARCH_PLUGIN_SANDBOX_AUTO_RSYNC", "0")
os.environ.setdefault("RESEARCH_PLUGIN_SANDBOX_REQUEST_WAIT", "20")

from backend.app import ResearchPluginApp  # noqa: E402
from backend.execution.backends.lambda_labs import LambdaCloudClient  # noqa: E402
from backend.execution.backends.lambda_labs.config import load_lambda_env_file  # noqa: E402


def run(
    argv: list[str],
    *,
    input_text: str | None = None,
    timeout: int = 120,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        argv,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            "command failed\n"
            f"argv={argv!r}\n"
            f"returncode={proc.returncode}\n"
            f"stdout={proc.stdout[-4000:]}\n"
            f"stderr={proc.stderr[-4000:]}"
        )
    return proc


def log(title: str, payload: object | None = None) -> None:
    print(f"\n--- {title} ---", flush=True)
    if payload is not None:
        if isinstance(payload, str):
            print(payload, flush=True)
        else:
            print(json.dumps(payload, indent=2, sort_keys=True), flush=True)


def ssh_parts(raw_command: str) -> tuple[list[str], str, str]:
    parts = shlex.split(raw_command)
    if not parts or parts[0] != "ssh" or len(parts) < 2:
        raise RuntimeError(f"unexpected raw ssh command: {raw_command!r}")
    target = parts[-1]
    transport = " ".join(shlex.quote(part) for part in parts[:-1])
    return parts, transport, target


def ssh(
    raw_command: str,
    command: str,
    *,
    input_text: str | None = None,
    timeout: int = 180,
    check: bool = True,
):
    parts, _, _ = ssh_parts(raw_command)
    return run([*parts, command], input_text=input_text, timeout=timeout, check=check)


def remote_notebook_lens(
    raw_command: str,
    args: str,
    *,
    stdin: str | None = None,
    timeout: int = 180,
    check: bool = True,
):
    command = (
        "cd ~/notebook-lens && mkdir -p notebooks .notebook_lens artifacts/lambda_remote_probe && "
        "export NL_EXPERIMENT_DIR=$PWD "
        "NL_NOTEBOOK_DIR=$PWD/notebooks "
        "NL_RUNTIME_DIR=$PWD/.notebook_lens "
        "NL_ARTIFACT_DIR=$PWD/artifacts/lambda_remote_probe "
        "NL_KERNEL_PYTHON=$PWD/.venv/bin/python && "
        ".venv/bin/notebook-lens "
        f"{args}"
    )
    return ssh(raw_command, command, input_text=stdin, timeout=timeout, check=check)


def setup_remote_repo(raw_command: str) -> None:
    _, transport, target = ssh_parts(raw_command)
    log("remote preflight")
    preflight = ssh(
        raw_command,
        "set -e; uname -a; command -v python3 || true; python3 --version || true; command -v uv || true",
        timeout=60,
    )
    print(preflight.stdout, flush=True)

    log("rsync notebook-lens repo")
    run(
        [
            "rsync",
            "-az",
            "--delete",
            "--exclude",
            ".venv",
            "--exclude",
            ".notebook_lens",
            "--exclude",
            ".git",
            "--exclude",
            "tmp",
            "--exclude",
            "artifacts",
            "--exclude",
            "notebooks",
            "-e",
            transport,
            f"{NOTEBOOK_LENS}/",
            f"{target}:~/notebook-lens/",
        ],
        timeout=300,
    )

    log("remote install")
    setup = r"""
set -euxo pipefail
cd ~/notebook-lens
if python3 - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
then
  PY=python3
else
  if command -v uv >/dev/null 2>&1; then
    uv python install 3.11
    PY="$(uv python find 3.11)"
  elif command -v python3.11 >/dev/null 2>&1; then
    PY=python3.11
  else
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends python3.11 python3.11-venv
    PY=python3.11
  fi
fi
"$PY" -m venv .venv
. .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
.venv/bin/notebook-lens env
"""
    installed = ssh(raw_command, setup, timeout=900)
    print(installed.stdout[-4000:], flush=True)


def rsync_to_remote(raw_command: str, local_path: Path, remote_path: str) -> None:
    _, transport, target = ssh_parts(raw_command)
    run(
        [
            "rsync",
            "-az",
            "-e",
            transport,
            str(local_path),
            f"{target}:{remote_path}",
        ],
        timeout=180,
    )


def write_local_cell(name: str, source: str) -> Path:
    cell_dir = RUN_DIR / "cells"
    cell_dir.mkdir(parents=True, exist_ok=True)
    path = cell_dir / name
    path.write_text(textwrap.dedent(source).strip() + "\n", encoding="utf-8")
    return path


def run_remote_cli_probe(raw_command: str) -> dict[str, object]:
    notebook = "notebooks/lambda_remote_probe.ipynb"
    results: dict[str, object] = {}

    def capture(
        name: str,
        args: str,
        *,
        stdin: str | None = None,
        timeout: int = 180,
        check: bool = True,
    ):
        proc = remote_notebook_lens(
            raw_command,
            args,
            stdin=stdin,
            timeout=timeout,
            check=check,
        )
        stream = proc.stdout.strip() or proc.stderr.strip()
        payload = None
        if stream.startswith("{"):
            try:
                payload = json.loads(stream)
            except json.JSONDecodeError:
                payload = None
        results[name] = {
            "returncode": proc.returncode,
            "json": payload,
            "stdout_tail": proc.stdout[-1800:],
            "stderr_tail": proc.stderr[-1800:],
        }
        return proc

    def require_json(name: str) -> dict[str, object]:
        payload = results.get(name, {}).get("json") if isinstance(results.get(name), dict) else None
        if not isinstance(payload, dict):
            raise RuntimeError(f"{name} did not return JSON: {results.get(name)}")
        if payload.get("schema_version") != 1:
            raise RuntimeError(f"{name} returned unexpected schema_version: {payload}")
        if payload.get("exit_code") not in (0, None):
            raise RuntimeError(f"{name} returned nonzero payload exit code: {payload}")
        return payload

    log("remote notebook-lens env")
    env = capture("env", f"env {shlex.quote(notebook)} --json")
    require_json("env")
    print(env.stdout, flush=True)

    log("remote new notebook")
    new = capture("new", f"new {shlex.quote(notebook)} --json")
    require_json("new")
    print(new.stdout, flush=True)

    log("remote stdin probe")
    stdin_probe = capture(
        "stdin_probe",
        "add-code notebooks/stdin_probe.ipynb --desc 'Probe SSH stdin forwarding' --file - --json",
        stdin="print('stdin reached notebook lens')\n",
        check=False,
    )
    print(stdin_probe.stdout, flush=True)
    stdin_state = capture(
        "stdin_probe_state",
        "state notebooks/stdin_probe.ipynb --outputs none --json",
        check=False,
    )
    print(stdin_state.stdout, flush=True)

    log("sync remote cell source files")
    ssh(raw_command, "mkdir -p ~/notebook-lens/tmp/lambda_remote_probe/cells", timeout=60)
    first_file = write_local_cell(
        "01_set_remote_state.py",
        """
        import os, socket
        from pathlib import Path
        x = 41
        artifact = Path(os.environ["NL_ARTIFACT_DIR"]) / "remote_metric.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(f"host={socket.gethostname()} x={x}\\n", encoding="utf-8")
        print("remote host", socket.gethostname())
        print("artifact", artifact)
        """,
    )
    second_file = write_local_cell(
        "02_reuse_remote_state.py",
        """
        print("x_plus_one", x + 1)
        """,
    )
    first_update_file = write_local_cell(
        "03_update_remote_state.py",
        """
        import os, socket
        from pathlib import Path
        x = 100
        artifact = Path(os.environ["NL_ARTIFACT_DIR"]) / "remote_metric.txt"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(f"host={socket.gethostname()} x={x}\\n", encoding="utf-8")
        print("updated remote host", socket.gethostname())
        print("updated artifact", artifact)
        """,
    )
    second_update_file = write_local_cell(
        "04_reuse_updated_remote_state.py",
        """
        print("x_plus_one", x + 1)
        """,
    )
    rsync_to_remote(raw_command, first_file, "~/notebook-lens/tmp/lambda_remote_probe/cells/01_set_remote_state.py")
    rsync_to_remote(raw_command, second_file, "~/notebook-lens/tmp/lambda_remote_probe/cells/02_reuse_remote_state.py")
    rsync_to_remote(raw_command, first_update_file, "~/notebook-lens/tmp/lambda_remote_probe/cells/03_update_remote_state.py")
    rsync_to_remote(raw_command, second_update_file, "~/notebook-lens/tmp/lambda_remote_probe/cells/04_reuse_updated_remote_state.py")

    log("remote add-code 1")
    first = capture(
        "add_code_1",
        f"add-code {shlex.quote(notebook)} --desc 'Set remote state and artifact' "
        "--file tmp/lambda_remote_probe/cells/01_set_remote_state.py --json",
    )
    first_payload = require_json("add_code_1")
    first_cell_id = first_payload["data"]["cell_id"]
    print(first.stdout, flush=True)

    log("remote add-code 2 reuses live kernel")
    second = capture(
        "add_code_2",
        f"add-code {shlex.quote(notebook)} --desc 'Reuse remote kernel state' "
        "--file tmp/lambda_remote_probe/cells/02_reuse_remote_state.py --json",
    )
    second_payload = require_json("add_code_2")
    second_cell_id = second_payload["data"]["cell_id"]
    print(second.stdout, flush=True)

    log("remote update-code 1 marks downstream stale")
    first_update = capture(
        "update_code_1",
        f"update-code {shlex.quote(notebook)} --id {shlex.quote(first_cell_id)} "
        "--desc 'Update remote state and artifact' "
        "--file tmp/lambda_remote_probe/cells/03_update_remote_state.py --json",
    )
    require_json("update_code_1")
    print(first_update.stdout, flush=True)

    log("remote update-code 2 refreshes dependent cell")
    second_update = capture(
        "update_code_2",
        f"update-code {shlex.quote(notebook)} --id {shlex.quote(second_cell_id)} "
        "--desc 'Reuse updated remote kernel state' "
        "--file tmp/lambda_remote_probe/cells/04_reuse_updated_remote_state.py --json",
    )
    require_json("update_code_2")
    print(second_update.stdout, flush=True)

    log("remote state skim")
    state = capture(
        "state_outputs_none",
        f"state {shlex.quote(notebook)} --outputs none --max-tokens 1000 --json",
    )
    require_json("state_outputs_none")
    print(state.stdout, flush=True)

    log("remote show-cell")
    shown = capture(
        "show_cell",
        f"show-cell {shlex.quote(notebook)} --id {shlex.quote(second_cell_id)} --outputs full --json",
    )
    require_json("show_cell")
    print(shown.stdout, flush=True)

    log("remote run-clean")
    clean = capture("run_clean", f"run-clean {shlex.quote(notebook)} --json")
    clean_payload = require_json("run_clean")
    if clean_payload["data"].get("status") != "ok":
        raise RuntimeError(f"run-clean failed: {clean_payload}")
    print(clean.stdout, flush=True)

    log("remote validate notebook json")
    validate = ssh(
        raw_command,
        "cd ~/notebook-lens && . .venv/bin/activate && python - <<'PY'\n"
        "import nbformat\n"
        "p='notebooks/lambda_remote_probe.ipynb'\n"
        "nb=nbformat.read(p, as_version=4)\n"
        "print('cells', len(nb.cells))\n"
        "print('last_output', nb.cells[-1].outputs[0].text.strip())\n"
        "assert nb.cells[-1].outputs[0].text.strip() == 'x_plus_one 101'\n"
        "PY",
    )
    results["validate"] = {
        "returncode": validate.returncode,
        "stdout_tail": validate.stdout[-1800:],
        "stderr_tail": validate.stderr[-1800:],
    }
    print(validate.stdout, flush=True)

    log("pull notebook and artifacts")
    _, transport, target = ssh_parts(raw_command)
    pull_dir = RUN_DIR / "pulled"
    if pull_dir.exists():
        import shutil

        shutil.rmtree(pull_dir)
    (pull_dir / "notebooks").mkdir(parents=True, exist_ok=True)
    (pull_dir / "artifacts").mkdir(parents=True, exist_ok=True)
    run(
        [
            "rsync",
            "-az",
            "-e",
            transport,
            f"{target}:~/notebook-lens/notebooks/",
            str(pull_dir / "notebooks") + "/",
        ],
        timeout=180,
    )
    run(
        [
            "rsync",
            "-az",
            "-e",
            transport,
            f"{target}:~/notebook-lens/artifacts/lambda_remote_probe/",
            str(pull_dir / "artifacts") + "/",
        ],
        timeout=180,
    )
    results["pulled"] = {
        "notebook": str(pull_dir / "notebooks" / "lambda_remote_probe.ipynb"),
        "artifact": str(pull_dir / "artifacts" / "remote_metric.txt"),
    }

    log("remote reset")
    reset = capture("reset", f"reset-kernel {shlex.quote(notebook)} --json")
    require_json("reset")
    print(reset.stdout, flush=True)

    return results


def main() -> int:
    load_lambda_env_file()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    temp_repo = Path(tempfile.mkdtemp(prefix="nl-lambda-rp-"))
    app = ResearchPluginApp(repo_root=temp_repo, db_path=temp_repo / ".research_plugin" / "state.sqlite")
    sandbox_id = ""
    project_id = ""
    experiment_id = ""

    try:
        log("backend health", app.sandboxes.health())
        project = app.call_tool("project.create", {"name": "Notebook Lens Lambda Probe"})
        project_id = project["id"]
        exp = app.call_tool(
            "experiment.create",
            {
                "project_id": project_id,
                "name": "notebook_lens_lambda_probe",
                "intent": "remote notebook lens probe",
            },
        )
        experiment_id = exp["id"]
        with app.store.transaction() as conn:
            conn.execute("UPDATE experiments SET status='ready_to_run' WHERE id=?", (experiment_id,))

        options = app.call_tool("sandbox.options", {"project_id": project_id})
        if not options.get("options"):
            raise RuntimeError("Lambda returned no currently available instance types")
        cheapest = options["options"][0]
        log(
            "selected lambda instance",
            {
                "instance_type": cheapest.get("instance_type"),
                "gpu": cheapest.get("gpu"),
                "price_usd_per_hour": cheapest.get("price_usd_per_hour"),
                "regions": cheapest.get("regions"),
            },
        )

        app.sandboxes.request_wait_seconds = 20.0
        created = app.call_tool(
            "sandbox.request",
            {
                "project_id": project_id,
                "experiment_id": experiment_id,
                "instance_type": cheapest["instance_type"],
                "time_limit": 1800,
            },
        )
        sandbox_id = str(created.get("sandbox_id") or "")
        log("provision request", {k: created.get(k) for k in ("status", "sandbox_id", "phase", "detail")})

        row = created
        deadline = time.monotonic() + 15 * 60
        while row.get("status") == "provisioning" and time.monotonic() < deadline:
            time.sleep(10)
            row = app.call_tool("sandbox.get", {"project_id": project_id, "experiment_id": experiment_id})
            log("provision poll", {k: row.get(k) for k in ("status", "phase", "detail")})
        if row.get("status") != "running":
            raise RuntimeError(f"sandbox did not reach running: {row}")

        sandbox_id = str(row.get("sandbox_id") or sandbox_id)
        log(
            "running sandbox",
            {
                "sandbox_id": sandbox_id,
                "instance_type": row.get("instance_type"),
                "region": row.get("region"),
                "gpu": row.get("gpu"),
                "cpu": row.get("cpu"),
                "memory": row.get("memory"),
            },
        )
        raw_ssh = row["ssh"]["raw_command"]
        setup_remote_repo(raw_ssh)
        results = run_remote_cli_probe(raw_ssh)
        output_path = RUN_DIR / "latest_results.json"
        output_path.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        log("wrote results", str(output_path))
        return 0
    finally:
        if experiment_id:
            try:
                released = app.call_tool(
                    "sandbox.release",
                    {"project_id": project_id, "experiment_id": experiment_id},
                )
                log("sandbox release", released)
            except Exception as exc:  # noqa: BLE001
                log("sandbox release failed", str(exc))
        if sandbox_id:
            try:
                terminated = LambdaCloudClient().terminate_instances([sandbox_id])
                log(
                    "direct terminate safety call",
                    [
                        {
                            "id": item.get("id"),
                            "name": item.get("name"),
                            "status": item.get("status"),
                        }
                        for item in terminated
                    ],
                )
            except Exception as exc:  # noqa: BLE001
                log("direct terminate safety call failed", str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
