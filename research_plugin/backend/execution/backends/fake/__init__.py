"""In-memory sandbox backend for SandboxService tests."""

from __future__ import annotations

import threading

from ...errors import BackendUnavailableError
from ...types import (
    BackendCapabilities,
    OnCreated,
    OnPhase,
    ProvisionedSandbox,
    SandboxRequest,
)


class FakeSandboxBackend:
    """Deterministic stand-in for ModalSandboxBackend.

    Tracks acquired sandboxes, liveness, terminations, and a per-experiment
    transcript so the registry's reuse/release/terminal logic can be exercised
    without Modal.

    Test knobs for the async provisioning path:
      - ``gate``: if set, ``acquire`` blocks at the "connecting" phase until the
        test sets the event — lets a test observe the `provisioning` state
        deterministically (no sleeps).
      - ``fail_after_create``: raise during the tunnel step (after the sandbox
        exists) to exercise failed-path cleanup / orphan termination.
      - ``fail_immediately``: raise before any sandbox is created.
    """

    def __init__(self) -> None:
        self.capabilities = BackendCapabilities(name="fake")
        self.counter = 0
        self.acquired: list[SandboxRequest] = []
        self.alive: dict[str, bool] = {}
        self.terminated: list[str] = []
        self.transcripts: dict[str, str] = {}
        self.by_experiment: dict[str, str] = {}
        # Live SSH endpoint per sandbox id; move_endpoint() simulates a tunnel
        # that Modal relocated so refresh_ssh_endpoint() can be exercised.
        self.endpoints: dict[str, tuple[str, int]] = {}
        # Observability dashboard URLs per sandbox id, mirroring Modal's
        # encrypted-tunnel surface. Empty by default; tests opt in by setting
        # the entry or calling move_dashboards() to simulate a relocation.
        self.dashboards: dict[str, dict[str, str]] = {}
        self.phases: list[tuple[str, str]] = []
        self.healthy = True
        # async-path knobs
        self.gate: threading.Event | None = None
        self.fail_after_create = False
        self.fail_immediately = False
        # metrics knob: per-sandbox-id sample dict (None => unavailable).
        self.metrics: dict[str, dict | None] = {}
        self.synced: list[dict] = []

    def acquire(
        self,
        *,
        request: SandboxRequest,
        on_phase: OnPhase | None = None,
        on_created: OnCreated | None = None,
    ) -> ProvisionedSandbox:
        self.acquired.append(request)
        if on_phase is not None:
            on_phase("syncing", "pushing repo to volume")
            self.phases.append(("syncing", request.experiment_id))
        if self.fail_immediately:
            raise BackendUnavailableError("fake create failure")
        if on_phase is not None:
            on_phase("creating", f"gpu={request.gpu or 'cpu'}")
        self.counter += 1
        sandbox_id = f"sb-{self.counter}"
        name = f"rp-{request.experiment_id}"
        self.alive[sandbox_id] = True
        self.by_experiment[request.experiment_id] = sandbox_id
        self.endpoints[sandbox_id] = ("sandbox.modal.test", 40000 + self.counter)
        workdir = request.remote_workdir or "/workspace/repo"
        # Past create: a failure must terminate the sandbox (mirrors Modal).
        try:
            if on_created is not None:
                on_created(sandbox_id, name)  # may raise to cancel
            if on_phase is not None:
                on_phase("connecting", "waiting for ssh")
            if self.gate is not None:
                self.gate.wait()
            if self.fail_after_create:
                raise BackendUnavailableError("fake tunnel failure")
        except BaseException:
            self.terminate(sandbox_id=sandbox_id)
            raise
        host, port = self.endpoints[sandbox_id]
        # Default fake-Modal dashboard URLs so the SandboxService persistence +
        # serializer path is exercised. A test wanting "no dashboards" can clear
        # ``self.dashboards[sandbox_id]``.
        self.dashboards.setdefault(
            sandbox_id,
            {
                "mlflow": f"https://mlflow-{sandbox_id}.modal.test",
                "tensorboard": f"https://tensorboard-{sandbox_id}.modal.test",
            },
        )
        return ProvisionedSandbox(
            sandbox_id=sandbox_id,
            ssh_host=host,
            ssh_port=port,
            ssh_user="root",
            workdir=workdir,
            volume_name=f"research-plugin-{request.project_id}",
            sandbox_data_dir="/workspace/sandbox_data",
            reused=False,
            dashboards=dict(self.dashboards[sandbox_id]),
        )

    def refresh_ssh_endpoint(self, *, sandbox_id: str) -> tuple[str, int] | None:
        if not self.alive.get(sandbox_id):
            return None
        return self.endpoints.get(sandbox_id)

    def dashboard_urls(self, *, sandbox_id: str) -> dict[str, str]:
        if not self.alive.get(sandbox_id):
            return {}
        return dict(self.dashboards.get(sandbox_id, {}))

    def find_sandbox_id(self, *, experiment_id: str) -> str | None:
        sandbox_id = self.by_experiment.get(experiment_id)
        if sandbox_id and self.alive.get(sandbox_id):
            return sandbox_id
        return None

    def is_alive(self, *, sandbox_id: str) -> bool:
        return bool(self.alive.get(sandbox_id, False))

    def terminate(self, *, sandbox_id: str) -> bool:
        self.alive[sandbox_id] = False
        self.terminated.append(sandbox_id)
        return True

    def read_transcript(
        self,
        *,
        sandbox_id: str,
        experiment_id: str,
        volume_name: str,
        workdir: str,
        tail: int | None = None,
    ) -> str:
        text = self.transcripts.get(experiment_id, "")
        if tail and tail > 0 and len(text) > tail:
            return text[-tail:]
        return text

    def sample_metrics(self, *, sandbox_id: str) -> dict | None:
        if not self.alive.get(sandbox_id):
            return None
        return self.metrics.get(sandbox_id)

    def sync_sandbox_files(
        self,
        *,
        project_id: str,
        sandbox_id: str,
        workdir: str,
        volume_name: str,
    ) -> dict:
        if not self.alive.get(sandbox_id):
            raise BackendUnavailableError("fake sandbox is not running")
        result = {
            "project_id": project_id,
            "sandbox_id": sandbox_id,
            "workdir": workdir,
            "volume": volume_name,
            "committed": True,
            "pushed": 0,
            "pulled": 0,
            "deleted_remote": 0,
            "deleted_local": 0,
            "conflicts": 0,
            "skipped_conflicts": [],
            "skipped_busy": False,
            "coalesced": False,
        }
        self.synced.append(result)
        return result

    def sandbox_environment(self) -> dict:
        return {"available_tokens": [], "notes": []}

    def health(self) -> dict:
        return {"ok": self.healthy, "name": self.capabilities.name}

    # ---- test helpers ----

    def kill(self, *, sandbox_id: str) -> None:
        """Simulate Modal reaping a sandbox (timeout / crash)."""
        self.alive[sandbox_id] = False

    def move_endpoint(self, *, sandbox_id: str, host: str, port: int) -> None:
        """Simulate Modal relocating a live sandbox's SSH tunnel."""
        self.endpoints[sandbox_id] = (host, port)

    def move_dashboards(self, *, sandbox_id: str, urls: dict[str, str]) -> None:
        """Simulate Modal relocating a live sandbox's encrypted dashboard tunnels."""
        self.dashboards[sandbox_id] = dict(urls)

    def append_transcript(self, *, experiment_id: str, text: str) -> None:
        self.transcripts[experiment_id] = self.transcripts.get(experiment_id, "") + text
