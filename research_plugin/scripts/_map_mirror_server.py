"""Local read-only MIRROR of real hosted projects, for map visual QA.

Pulls the full hydrated graph of one or more real projects from the hosted
control plane's read API and reconstructs the rows in a fresh LOCAL SQLite so
the real MapService + sibling services render REAL data unmodified. Writes
NOTHING to production — it only GETs.

Usage:
  TOKEN=... PYTHONPATH=. .venv/bin/python scripts/_map_mirror_server.py \
      proj_5790a0dfe89f proj_6bfeb751afd3 ...
Serves on MAP_MIRROR_PORT (default 8810).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

import uvicorn

from backend.composition import build_local_server
from backend.execution.backends.fake import FakeSandboxBackend
from backend.state import StateStore
from backend.state.store import next_created_seq
from backend.storage.blobs import LocalDirBlobStore

HOST = os.environ.get("MAP_MIRROR_HOST", "https://experiments.rapidreview.io")
TOKEN = os.environ.get("TOKEN", "")
TMP = Path(tempfile.mkdtemp(prefix="map_mirror_"))

# Projects mirrored but stashed out of the UI list (settings.hidden = true).
# Defaults to the bio projects (EEG-Foundations, ProteinGym Spike RBD); the
# rows and direct-by-id access are retained, they just don't appear in the
# project picker. Override with HIDE_PIDS="proj_a,proj_b".
HIDDEN_PIDS = {
    p.strip()
    for p in os.environ.get(
        "HIDE_PIDS", "proj_5790a0dfe89f,proj_6bfeb751afd3"
    ).split(",")
    if p.strip()
}


def _get(path: str) -> dict:
    req = urllib.request.Request(
        f"{HOST}{path}",
        headers={"Authorization": f"Bearer {TOKEN}", "X-RP-Client-Version": "0.0010"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _mirror_project(app, pid: str) -> None:
    home = _get(f"/api/projects/{pid}/home")
    reflections = _get(f"/api/projects/{pid}/reflections").get("syntheses", [])
    project = home["project"]
    claims = home.get("claims", [])
    experiments = home.get("experiments", [])
    resources = home.get("resources", [])

    settings_json = '{"hidden": true}' if pid in HIDDEN_PIDS else "{}"
    with app.store.transaction() as conn:
        conn.execute(
            "INSERT INTO projects (id, name, summary, status, settings_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (project["id"], project.get("name", pid), project.get("summary", ""),
             project.get("status", "active"), settings_json,
             project.get("created_at", "2026-01-01T00:00:00Z")),
        )
        for c in claims:
            conn.execute(
                "INSERT INTO claims (id, project_id, statement, scope, status, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (c["id"], pid, c.get("statement", ""), c.get("scope", ""), c.get("status", "active"),
                 c.get("confidence", "medium"), c.get("created_at", project["created_at"])),
            )

        review_rows: list[dict] = []
        for e in experiments:
            conn.execute(
                """INSERT INTO experiments (id, project_id, name, intent, status, attempt_index,
                   conclusion, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (e["id"], pid, e.get("name", ""), e.get("intent", ""), e.get("status", "planned"),
                 int(e.get("attempt_index") or 1), e.get("conclusion", ""),
                 e.get("created_at", project["created_at"]), e.get("updated_at", e.get("created_at", project["created_at"]))),
            )
            for tc in e.get("tested_claims", []):
                conn.execute(
                    "INSERT OR IGNORE INTO experiment_claims (experiment_id, claim_id) VALUES (?, ?)",
                    (e["id"], tc["id"]),
                )
            for rev in e.get("reviews", []):
                rev = dict(rev)
                rev.setdefault("target_type", "experiment")
                rev.setdefault("target_id", e["id"])
                review_rows.append(rev)

        seen_res: set[str] = set()

        def _insert_resource(r: dict) -> None:
            if r["id"] in seen_res:
                return
            seen_res.add(r["id"])
            conn.execute(
                """INSERT INTO resources (id, project_id, path, kind, title, version_token, mtime_ns,
                   size_bytes, observed_at, missing, deleted, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'mirror', ?, ?)""",
                (r["id"], pid, r.get("path", ""), r.get("kind", "file"), r.get("title", ""),
                 r.get("version_token", r["id"]), int(r.get("mtime_ns") or 0), int(r.get("size_bytes") or 0),
                 r.get("observed_at", project["created_at"]), 1 if r.get("missing") else 0,
                 r.get("created_at", project["created_at"]), r.get("updated_at", project["created_at"])),
            )

        assoc_seq = 0
        for r in resources:
            _insert_resource(r)
        # Experiment-scoped resources carry association metadata via get_state.
        for e in experiments:
            for r in e.get("resources", []):
                _insert_resource(r)
                assoc_seq += 1
                # version_id left NULL: resource_versions aren't mirrored and the
                # column FKs to them; the map never reads it.
                conn.execute(
                    """INSERT OR IGNORE INTO resource_associations (id, resource_id, version_id, target_type,
                       target_id, role, attempt_index, created_at, created_seq)
                       VALUES (?, ?, NULL, 'experiment', ?, ?, ?, ?, ?)""",
                    (f"assoc_{assoc_seq}", r["id"], e["id"],
                     r.get("association_role", "other"), int(r.get("association_attempt_index") or 1),
                     project["created_at"], assoc_seq),
                )

        for i, rev in enumerate(review_rows):
            rq_id = rev.get("request_id") or f"rr_mirror_{i}"
            rs_id = rev.get("session_id") or f"rvs_mirror_{i}"
            conn.execute(
                """INSERT OR IGNORE INTO review_requests (id, project_id, target_type, target_id, role,
                   capability_hash, status, target_snapshot_id, expires_at, created_at, created_seq)
                   VALUES (?, ?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?)""",
                (rq_id, pid, rev.get("target_type"), rev.get("target_id"), rev.get("role", "experiment_reviewer"),
                 f"cap_mirror_{pid}_{i}", rev.get("target_snapshot_id", "snap"), project["created_at"],
                 rev.get("created_at", project["created_at"]), i + 1),
            )
            conn.execute(
                """INSERT OR IGNORE INTO review_sessions (id, request_id, independence, status, created_at)
                   VALUES (?, ?, 'attested_agent_review', 'completed', ?)""",
                (rs_id, rq_id, rev.get("created_at", project["created_at"])),
            )
            conn.execute(
                """INSERT INTO reviews (id, project_id, request_id, session_id, target_snapshot_id,
                   target_type, target_id, role, verdict, synopsis, notes, created_at, created_seq)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rev["id"], pid, rq_id, rs_id, rev.get("target_snapshot_id", "snap"),
                 rev.get("target_type"), rev.get("target_id"), rev.get("role", "experiment_reviewer"),
                 rev.get("verdict", "pass"), rev.get("synopsis", ""), rev.get("notes", ""),
                 rev.get("created_at", project["created_at"]), i + 1),
            )

        for i, syn in enumerate(reflections):
            conn.execute(
                """INSERT INTO reflections (id, project_id, title, status, attempt_index, published_at,
                   created_at, updated_at, created_seq) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (syn["id"], pid, syn.get("title", ""), syn.get("status", "published"),
                 int(syn.get("attempt_index") or 1), syn.get("published_at"),
                 syn.get("created_at", project["created_at"]), syn.get("updated_at", project["created_at"]), i + 1),
            )
            claim_ids = {c["id"] for c in claims}
            exp_ids = {e["id"] for e in experiments}
            for mc in syn.get("materialized_claims", []):
                cid = mc.get("claim_id", mc.get("id"))
                if cid in claim_ids:  # FK to claims; skip references outside the snapshot
                    conn.execute(
                        "INSERT OR IGNORE INTO synthesis_claim_changes (synthesis_id, claim_id, op, created_at) VALUES (?, ?, ?, ?)",
                        (syn["id"], cid, mc.get("op", "update"), project["created_at"]),
                    )
            for me in syn.get("materialized_experiments", []):
                eid = me.get("experiment_id", me.get("id"))
                if eid in exp_ids:
                    conn.execute(
                        "INSERT OR IGNORE INTO synthesis_experiments (synthesis_id, experiment_id, created_at) VALUES (?, ?, ?)",
                        (syn["id"], eid, project["created_at"]),
                    )

        # Freshness: one event per entity at its real last-touched time so the
        # glow/desaturation reflects true recency (last_touched_reader groups
        # MAX(created_at) per target_id over the events table).
        def _touch(target_id: str, ts: str) -> None:
            conn.execute(
                "INSERT INTO events (project_id, type, target_type, target_id, created_at) VALUES (?, 'mirror.touch', '', ?, ?)",
                (pid, target_id, ts),
            )

        for e in experiments:
            _touch(e["id"], e.get("updated_at", e.get("created_at", project["created_at"])))
        for c in claims:
            _touch(c["id"], c.get("created_at", project["created_at"]))
        for r in resources:
            _touch(r["id"], r.get("updated_at", project["created_at"]))
        for rev in review_rows:
            _touch(rev["id"], rev.get("created_at", project["created_at"]))
        for syn in reflections:
            _touch(syn["id"], syn.get("updated_at", project["created_at"]))

    n = app.research_map.state(project_id=pid)
    print(f"mirrored {project['name'][:40]:40} {pid}  ->  {len(n['entities'])} map entities")


def build(pids: list[str]):
    brain_dir = TMP / ".research_plugin"
    server = build_local_server(
        state_dir=brain_dir,
        env={},
        execution_backend=FakeSandboxBackend(),
        store=StateStore(db_path=brain_dir / "state.sqlite"),
        blobs=LocalDirBlobStore(root=brain_dir / "blobs"),
    )
    for pid in pids:
        try:
            _mirror_project(server.app, pid)
        except Exception as exc:  # noqa: BLE001
            print(f"skip {pid}: {exc}")
    return server


if __name__ == "__main__":
    pids = sys.argv[1:] or ["proj_5790a0dfe89f"]
    if not TOKEN:
        raise SystemExit("set TOKEN env to the hosted API bearer token")
    server = build(pids)
    port = int(os.environ.get("MAP_MIRROR_PORT", "8810"))
    print(f"mirror serving on http://127.0.0.1:{port}")
    uvicorn.run(server.fastapi_app, host="127.0.0.1", port=port, log_level="warning")
