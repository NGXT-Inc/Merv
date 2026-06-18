"""Run a stable HTTP daemon (no autoreload) against the seeded repo."""
import sys
from pathlib import Path

import uvicorn

from backend.app import ResearchPluginApp
from backend.http_api import create_fastapi_app
from backend.execution.backends.fake import FakeSandboxBackend
from tests.fakes import FakeRsyncSyncer

REPO = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/rsui-seed-repo")
app = ResearchPluginApp(
    repo_root=REPO,
    db_path=REPO / ".research_plugin" / "state.sqlite",
    execution_backend=FakeSandboxBackend(),
    rsync_syncer=FakeRsyncSyncer(sync_pulled=1, sync_stdout="metrics.json\n"),
)
uvicorn.run(create_fastapi_app(app), host="127.0.0.1", port=8787, log_level="warning")
