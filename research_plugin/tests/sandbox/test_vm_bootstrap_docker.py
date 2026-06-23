"""Docker-simulated VM bootstrap integration (cloud plan Phase 5).

Applies the REAL Lambda-style phase-1 bootstrap (``build_bootstrap_core`` —
the exact fragment ``build_user_data`` ships) inside a small sshd container,
then exercises the dual-key contract end to end with the real backend code
paths:

  (a) a user-key SSH command goes through the rec.sh ForceCommand and is
      recorded to the transcript;
  (b) a management-key transcript read (``read_transcript``) works and is
      NOT recorded — the Match-exempt principal replaces the prefix bypass;
  (c) ``run_parachute`` tars the experiment dir honoring the shared
      excludes/caps and PUTs it to an ephemeral HTTP server started here
      (standing in for the presigned URL);
  (d) the worker's ``parachute_restore`` task lands those bytes in the
      local experiment folder.

Skipped cleanly when docker is unavailable (a fast ``docker info`` probe).
The helper image is built once and cached as ``rp-test-sshd:bookworm``;
container state is shared across the ordered test methods (test_01..test_04).
"""

from __future__ import annotations

import hashlib
import io
import shutil
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from backend.dataplane import InProcessTaskChannel, LocalDataPlaneWorker
from backend.execution.backends.fake import FakeSandboxBackend
from backend.execution.backends.lambda_labs.sandbox_backend import (
    LambdaLabsSandboxBackend,
)
from backend.execution.vm_bootstrap import build_bootstrap_core
from backend.workspace import LocalWorkspace


IMAGE = "rp-test-sshd:bookworm"
EXPERIMENT_ID = "exp_t"
WORKDIR = "/workspace/exp_t"
SESSIONS_DIR = "/workspace/.research_plugin_sessions/exp_t"
DATA_DIR = "/workspace/data"
TRANSCRIPT = f"{SESSIONS_DIR}/transcript.log"
MARKER = "hello-recorded-marker"

DOCKERFILE = """\
FROM debian:bookworm-slim
RUN apt-get update \\
    && apt-get install -y --no-install-recommends \\
       openssh-server sudo curl ca-certificates \\
    && rm -rf /var/lib/apt/lists/* \\
    && mkdir -p /run/sshd
CMD ["sleep", "infinity"]
"""


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "info"], capture_output=True, timeout=10
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


HAVE_DOCKER = _docker_available()


def _ensure_image() -> None:
    if (
        subprocess.run(
            ["docker", "image", "inspect", IMAGE], capture_output=True
        ).returncode
        == 0
    ):
        return
    with tempfile.TemporaryDirectory() as context:
        (Path(context) / "Dockerfile").write_text(DOCKERFILE)
        subprocess.run(
            ["docker", "build", "-t", IMAGE, context],
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )


class _PutHandler(BaseHTTPRequestHandler):
    """Ephemeral presigned-PUT stand-in: stores each PUT body by path."""

    protocol_version = "HTTP/1.1"
    received: dict[str, bytes] = {}

    def do_PUT(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler contract
        length = int(self.headers.get("Content-Length", "0"))
        type(self).received[self.path] = self.rfile.read(length)
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args) -> None:  # noqa: D102 — keep test output clean
        del args


@unittest.skipUnless(HAVE_DOCKER, "docker is not available")
class VmBootstrapDockerTest(unittest.TestCase):
    container: str = ""
    ssh_port: int = 0
    parachute_bytes: bytes | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.user_key = base / "user_key"
        cls.mgmt_key = base / "mgmt_key"
        for key_path, comment in ((cls.user_key, "user"), (cls.mgmt_key, "mgmt")):
            subprocess.run(
                [
                    "ssh-keygen", "-t", "ed25519", "-N", "", "-q",
                    "-C", f"rp-docker-{comment}", "-f", str(key_path),
                ],
                check=True,
                capture_output=True,
            )
        _ensure_image()
        run = subprocess.run(
            [
                "docker", "run", "-d", "--rm",
                "-p", "127.0.0.1:0:22",
                # Lets the container's curl reach the test's HTTP server on
                # the host (native-Linux docker needs the explicit mapping;
                # Docker Desktop ships the name anyway).
                "--add-host", "host.docker.internal:host-gateway",
                IMAGE, "sleep", "infinity",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        cls.container = run.stdout.strip()
        port_line = subprocess.run(
            ["docker", "port", cls.container, "22/tcp"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().splitlines()[0]
        cls.ssh_port = int(port_line.rsplit(":", 1)[1])
        # The REAL phase-1 bootstrap, exactly as build_user_data ships it.
        core = build_bootstrap_core(
            public_key=cls.user_key.with_suffix(".pub").read_text().strip(),
            management_public_key=cls.mgmt_key.with_suffix(".pub").read_text().strip(),
            experiment_id=EXPERIMENT_ID,
            workdir=WORKDIR,
            sessions_dir=SESSIONS_DIR,
            sandbox_data_dir=DATA_DIR,
        )
        bootstrap = (
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            + core
            + "\n/usr/sbin/sshd || true\n"
        )
        cls._exec(bootstrap, check=True)
        cls._wait_for_mgmt_ssh()

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.container:
            subprocess.run(
                ["docker", "rm", "-f", cls.container], capture_output=True
            )
        cls.tmp.cleanup()

    # ---------- helpers ----------

    @classmethod
    def _exec(
        cls, script: str, *, check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["docker", "exec", "-i", cls.container, "bash", "-s"],
            input=script,
            text=True,
            capture_output=True,
            timeout=120,
            check=check,
        )

    @classmethod
    def _ssh(cls, *, key: Path, user: str, command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "ssh",
                "-i", str(key),
                "-p", str(cls.ssh_port),
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=no",
                "-o", "UserKnownHostsFile=/dev/null",
                "-o", "ConnectTimeout=10",
                f"{user}@127.0.0.1",
                command,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )

    @classmethod
    def _wait_for_mgmt_ssh(cls, timeout: float = 60.0) -> None:
        # Readiness is probed over the MANAGEMENT principal so the wait never
        # pollutes the transcript the recording assertions inspect.
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = cls._ssh(key=cls.mgmt_key, user="rpmgmt", command="true")
            if last.returncode == 0:
                return
            time.sleep(1.0)
        raise AssertionError(
            f"sshd in the container never became reachable: {last and last.stderr}"
        )

    def _transcript(self) -> str:
        result = self._exec(f"cat {TRANSCRIPT} 2>/dev/null || true")
        return result.stdout

    # ---------- the ordered flow ----------

    def test_01_user_key_command_is_recorded(self) -> None:
        result = self._ssh(
            key=self.user_key, user="root", command=f"echo {MARKER}"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(MARKER, result.stdout)
        log = self._transcript()
        self.assertIn(f"$ echo {MARKER}", log)
        self.assertIn("(exit 0)", log)

    def test_02_mgmt_transcript_read_works_and_is_unrecorded(self) -> None:
        backend = LambdaLabsSandboxBackend()
        text = backend.read_transcript(
            sandbox_id="docker-vm",
            experiment_id=EXPERIMENT_ID,
            volume_name="",
            workdir=WORKDIR,
            ssh_host="127.0.0.1",
            ssh_port=self.ssh_port,
            ssh_user="root",  # ignored: the management channel has its own principal
            key_path=str(self.mgmt_key),
        )
        self.assertIn(MARKER, text)
        # The read itself never lands in the transcript: the Match-exempt
        # principal bypasses rec.sh, so polling cannot re-ingest the log.
        log = self._transcript()
        self.assertNotIn("tail -c", log)
        self.assertEqual(log.count("$ "), 1)
        # The management key cannot log in as the user principal (key
        # separation is real, not cosmetic).
        denied = self._ssh(key=self.mgmt_key, user="root", command="true")
        self.assertNotEqual(denied.returncode, 0)

    def test_03_parachute_uploads_with_the_shared_excludes(self) -> None:
        seed = f"""
set -euo pipefail
mkdir -p {WORKDIR}/.git {WORKDIR}/artifacts_to_keep {DATA_DIR}/.rp_runs/run1
printf 'keep\\n' > {WORKDIR}/results.json
printf 'gitstuff\\n' > {WORKDIR}/.git/config
printf 'ckpt\\n' > {WORKDIR}/model.pt
printf 'weights\\n' > {WORKDIR}/artifacts_to_keep/weights.dat
printf 'secret-env\\n' > {DATA_DIR}/.rp_runs/run1/env
truncate -s 101M {WORKDIR}/big.blob
"""
        self._exec(seed, check=True)
        _PutHandler.received.clear()
        server = ThreadingHTTPServer(("0.0.0.0", 0), _PutHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            put_url = (
                f"http://host.docker.internal:{server.server_address[1]}/parachute"
            )
            receipt = LambdaLabsSandboxBackend().run_parachute(
                sandbox_id="docker-vm",
                put_url=put_url,
                ssh_host="127.0.0.1",
                ssh_port=self.ssh_port,
                key_path=str(self.mgmt_key),
            )
        finally:
            server.shutdown()
            server.server_close()
        body = _PutHandler.received.get("/parachute")
        self.assertIsNotNone(body, "the parachute never PUT its tar")
        assert body is not None
        self.assertEqual(receipt["sha256"], hashlib.sha256(body).hexdigest())
        self.assertEqual(receipt["size_bytes"], len(body))
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
            names = {member.name.lstrip("./") for member in tar.getmembers()}
        self.assertIn("results.json", names)
        self.assertIn("artifacts_to_keep/weights.dat", names)
        # The shared excludes and size caps held on a real GNU tar.
        self.assertNotIn("model.pt", names)
        self.assertNotIn("big.blob", names)
        self.assertFalse(any(".git" in name.split("/") for name in names))
        # Scope invariant: the data dir (and its .rp_runs env dumps) never
        # rides the parachute.
        self.assertFalse(any(".rp_runs" in name for name in names))
        type(self).parachute_bytes = body

    def test_04_restore_lands_the_files_in_the_experiment_folder(self) -> None:
        body = type(self).parachute_bytes
        if body is None:
            self.fail("test_03 did not capture a parachute upload")
        with tempfile.TemporaryDirectory() as repo:
            worker = LocalDataPlaneWorker(
                workspace=LocalWorkspace(repo_root=Path(repo)),
                backend=FakeSandboxBackend(),
            )
            channel = InProcessTaskChannel(worker=worker)
            result = channel.submit(
                task_type="parachute_restore",
                payload={
                    "experiment_id": EXPERIMENT_ID,
                    "name": EXPERIMENT_ID,
                    "data": body,
                },
            )
            folder = Path(repo) / "experiments" / EXPERIMENT_ID
            self.assertEqual((folder / "results.json").read_text(), "keep\n")
            self.assertEqual(
                (folder / "artifacts_to_keep" / "weights.dat").read_text(),
                "weights\n",
            )
            self.assertFalse((folder / "model.pt").exists())
            self.assertFalse((folder / ".git").exists())
            self.assertGreaterEqual(int(result["restored"]), 2)


if __name__ == "__main__":
    unittest.main()
