"""Throwaway demo server: a rich, realistic research board for the map.

Seeds one project with a ~60-entity graph shaped like ten weeks of real work —
a supported direction, a hot cluster mid-run, and a dead, stale corner — so
every zoom register, freshness trace, and edge kind has something to show.
Statuses and timestamps are written directly to the store (demo-only shortcut
past the gate machinery). For local visualization only — not part of the
product.

Run:  PYTHONPATH=. MAP_DEMO_PORT=8787 .venv/bin/python scripts/_map_demo_server.py
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn

from backend.composition import build_local_server
from backend.execution.backends.fake import FakeSandboxBackend
from backend.state import StateStore
from backend.storage.blobs import LocalDirBlobStore
from backend.utils import new_id

NOW = datetime.fromtimestamp(
    int(os.environ.get("MAP_DEMO_NOW_MS", "0")) / 1000 or time.time(), tz=timezone.utc
)
TMP = Path(tempfile.mkdtemp(prefix="map_demo_"))


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


# (key, statement, status, confidence, days_ago)
CLAIMS = [
    ("c_sparse", "Block-sparse attention preserves quality above 90% sparsity", "supported", "high", 70),
    ("c_kv", "KV-cache pruning is safe up to 50% on retrieval tasks", "weakened", "medium", 62),
    ("c_linear", "Linear attention matches softmax on long-context QA", "contradicted", "low", 58),
    ("c_hybrid", "Hybrid local+global heads beat uniform sparsity at equal FLOPs", "active", "medium", 21),
    ("c_router", "Learned sparse routers transfer across context lengths", "active", "low", 14),
    ("c_quant", "8-bit KV quantization is quality-neutral end to end", "supported", "medium", 40),
    ("c_curr", "Context-length curriculum stabilizes long-context training", "draft", "low", 2),
    ("c_mem", "Memory tokens recover global information lost to sparsity", "active", "medium", 18),
]

# (key, claim_key, name, intent, status, conclusion, created_days_ago, touched_days_ago)
EXPERIMENTS = [
    ("e1", "c_sparse", "sparsity_sweep", "Sweep block sparsity 50-95% on 8k contexts", "complete",
     "Quality flat to 92% sparsity; cliff beyond.", 68, 55),
    ("e2", "c_sparse", "sparse_16k", "Replicate sparsity sweep at 16k context", "complete",
     "Holds at 16k; cliff moves to 90%.", 52, 44),
    ("e3", "c_sparse", "sparse_topk_ablate", "Ablate top-k vs blocked patterns", "failed",
     "Top-k kernels OOM above 8k; blocked only.", 48, 41),
    ("e18", "c_sparse", "sparse_needle", "Needle-in-haystack probe at 95% sparsity", "complete",
     "Retrieval intact at 92%, degrades at 95%.", 6, 1.5),
    ("e4", "c_kv", "kv_prune_50", "Prune 50% of KV cache on retrieval suite", "complete",
     "50% safe on 3 of 4 tasks; multi-hop regresses 4pts.", 58, 33),
    ("e5", "c_kv", "kv_prune_multihop", "Isolate multi-hop regression under pruning", "experiment_review",
     "", 9, 0.8),
    ("e6", "c_linear", "linear_qa_bench", "Linear attention on LongQA bench", "failed",
     "12pt gap vs softmax; not closable with LR sweeps.", 55, 47),
    ("e7", "c_linear", "linear_hybrid_rescue", "Rescue linear attention with softmax first layers", "abandoned",
     "Gap persists; direction killed.", 49, 42),
    ("e8", "c_hybrid", "hybrid_flops_match", "FLOP-matched hybrid vs uniform sparsity", "running",
     "", 4, 0.1),
    ("e9", "c_hybrid", "hybrid_head_ratio", "Sweep local:global head ratios", "complete",
     "3:1 local:global optimal at 32k.", 16, 3),
    ("e10", "c_hybrid", "hybrid_scaleup", "Scale hybrid pattern to 1B params", "planned",
     "", 1.2, 0.3),
    ("e11", "c_router", "router_transfer", "Train router at 8k, eval at 32k", "running",
     "", 5, 0.2),
    ("e12", "c_router", "router_capacity", "Router capacity vs sparsity ratio", "design_review",
     "", 2.5, 0.5),
    ("e13", "c_quant", "kv_int8_e2e", "End-to-end int8 KV quantization", "complete",
     "Neutral on all suites; ship it.", 38, 30),
    ("e17", "c_quant", "kv_int4_probe", "Push KV quantization to int4", "failed",
     "int4 breaks retrieval; 8-bit is the floor.", 34, 28),
    ("e14", "c_curr", "curriculum_pilot", "Pilot 2k->32k curriculum schedule", "planned",
     "", 1.0, 0.4),
    ("e15", "c_mem", "memtok_recover", "Add 64 memory tokens to 92% sparse model", "ready_to_run",
     "", 7, 1.0),
    ("e16", "c_mem", "memtok_count_sweep", "Sweep memory token count 8-256", "complete",
     "64 tokens recover 90% of the gap.", 15, 8),
]

# (exp_key, path, role, title)  -- registered as repo-file resources
RESOURCES = [
    ("e1", "experiments/sparsity_sweep/plan.md", "plan", "Sparsity sweep plan"),
    ("e1", "experiments/sparsity_sweep/report.md", "report", "Sparsity sweep report"),
    ("e1", "experiments/sparsity_sweep/results/quality.json", "result", "Quality vs sparsity"),
    ("e2", "experiments/sparse_16k/report.md", "report", "16k replication report"),
    ("e3", "experiments/sparse_topk_ablate/report.md", "report", "Top-k ablation failure notes"),
    ("e18", "experiments/sparse_needle/results/needle.json", "result", "Needle retrieval curve"),
    ("e4", "experiments/kv_prune_50/report.md", "report", "KV pruning report"),
    ("e4", "experiments/kv_prune_50/results/tasks.json", "result", "Per-task pruning deltas"),
    ("e5", "experiments/kv_prune_multihop/plan.md", "plan", "Multi-hop isolation plan"),
    ("e6", "experiments/linear_qa_bench/report.md", "report", "Linear attention bench report"),
    ("e8", "experiments/hybrid_flops_match/plan.md", "plan", "FLOP-match protocol"),
    ("e9", "experiments/hybrid_head_ratio/report.md", "report", "Head ratio sweep report"),
    ("e9", "experiments/hybrid_head_ratio/results/ratios.json", "result", "Ratio grid results"),
    ("e11", "experiments/router_transfer/plan.md", "plan", "Router transfer plan"),
    ("e13", "experiments/kv_int8_e2e/report.md", "report", "int8 KV report"),
    ("e15", "experiments/memtok_recover/plan.md", "plan", "Memory token recovery plan"),
    ("e16", "experiments/memtok_count_sweep/report.md", "report", "Token count sweep report"),
    (None, "notes/reading_log.md", "note", "Reading log"),
    (None, "notes/compute_budget.md", "note", "Compute budget"),
    (None, "project_logic_graph.json", "project_graph", "Project logic graph"),
]

# (exp_key, role, verdict, synopsis, days_ago)
REVIEWS = [
    ("e1", "design_reviewer", "pass", "Sweep design is sound; grid covers the cliff region.", 66),
    ("e1", "experiment_reviewer", "pass", "Numbers support the claim through 92%.", 54),
    ("e4", "experiment_reviewer", "needs_changes", "Multi-hop regression needs isolation before the claim holds.", 32),
    ("e6", "experiment_reviewer", "fail", "Gap is real and robust; claim contradicted.", 46),
    ("e9", "experiment_reviewer", "pass", "Ratio conclusion reproduces across seeds.", 2.8),
    ("e13", "experiment_reviewer", "pass", "Quantization neutrality holds on every suite.", 29),
    ("e12", "design_reviewer", "needs_changes", "Capacity grid too coarse at high sparsity.", 0.6),
    ("e16", "experiment_reviewer", "pass", "Recovery curve is clean; 64-token knee confirmed.", 7.5),
    ("e18", "experiment_reviewer", "pass", "Needle probe convincing; cliff localized.", 1.1),
]

# (key, title, status, created_days_ago, claim_keys, exp_keys)
REFLECTIONS = [
    ("syn1", "Wave 1: sparsity holds, linear attention dies", "published", 36,
     ["c_sparse", "c_linear"], ["e6", "e7"]),
    ("syn2", "Wave 2: hybrid patterns are the frontier", "reflecting", 0.7,
     ["c_hybrid", "c_router"], ["e8", "e11"]),
]


def build():
    brain_dir = TMP / ".research_plugin"
    server = build_local_server(
        state_dir=brain_dir,
        env={},
        execution_backend=FakeSandboxBackend(),
        store=StateStore(db_path=brain_dir / "state.sqlite"),
        blobs=LocalDirBlobStore(root=brain_dir / "blobs"),
    )
    app = server.app
    pid = app.call_tool("project.create", {
        "name": "Sparse Attention for Long-Context Models",
        "summary": "Which sparsity patterns keep quality while cutting long-context cost?",
    })["id"]

    claim_ids: dict[str, str] = {}
    for key, statement, status, confidence, _days in CLAIMS:
        claim_ids[key] = app.call_tool("claim.create", {"project_id": pid, "statement": statement})["id"]
        if (status, confidence) != ("active", "medium"):
            app.call_tool("claim.update", {
                "project_id": pid, "claim_id": claim_ids[key],
                "status": status, "confidence": confidence,
            })

    # Experiments go straight into the store: the demo wants ten weeks of
    # workflow outcomes (statuses, conclusions, staggered timestamps) without
    # walking every gate (active-experiment cap, reflection threshold).
    exp_ids: dict[str, str] = {}
    with app.store.transaction() as conn:
        for key, claim_key, name, intent, status, conclusion, created, touched in EXPERIMENTS:
            exp_ids[key] = new_id(prefix="exp")
            conn.execute(
                """
                INSERT INTO experiments (id, project_id, name, intent, status, conclusion, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (exp_ids[key], pid, name, intent, status, conclusion, iso(created), iso(touched)),
            )
            conn.execute(
                "INSERT INTO experiment_claims (experiment_id, claim_id) VALUES (?, ?)",
                (exp_ids[key], claim_ids[claim_key]),
            )
            # One event per experiment so freshness reflects touched, not created.
            app.store.record_event(
                conn=conn, project_id=pid, event_type="experiment.transitioned",
                target_type="experiment", target_id=exp_ids[key],
            )

    with app.store.transaction() as conn:
        for (key, _s, status, _conf, days), _ in zip(CLAIMS, CLAIMS):
            conn.execute(
                "UPDATE claims SET created_at = ? WHERE id = ?",
                (iso(days), claim_ids[key]),
            )

        res_seq = 0
        for exp_key, path, role, title in RESOURCES:
            res_seq += 1
            res_id = new_id(prefix="res")
            created = iso(EXPERIMENTS[[e[0] for e in EXPERIMENTS].index(exp_key)][6] - 0.5) if exp_key else iso(30)
            conn.execute(
                """
                INSERT INTO resources (id, project_id, path, kind, title, version_token, mtime_ns,
                                       size_bytes, observed_at, missing, deleted, created_by, created_at, updated_at)
                VALUES (?, ?, ?, 'file', ?, ?, 0, 2048, ?, 0, 0, 'demo', ?, ?)
                """,
                (res_id, pid, path, title, f"demo-{res_seq}", created, created, created),
            )
            if exp_key:
                conn.execute(
                    """
                    INSERT INTO resource_associations (id, resource_id, target_type, target_id, role,
                                                       attempt_index, created_at, created_seq)
                    VALUES (?, ?, 'experiment', ?, ?, 1, ?, ?)
                    """,
                    (new_id(prefix="assoc"), res_id, exp_ids[exp_key], role, created, res_seq),
                )

        for index, (exp_key, role, verdict, synopsis, days) in enumerate(REVIEWS):
            request_id, session_id = new_id(prefix="rr"), new_id(prefix="rvs")
            capability_hash = hashlib.sha256(f"demo-{index}".encode()).hexdigest()
            conn.execute(
                """
                INSERT INTO review_requests (id, project_id, target_type, target_id, role, reason,
                                             capability_hash, status, target_snapshot_id, expires_at,
                                             created_at, created_seq)
                VALUES (?, ?, 'experiment', ?, ?, 'demo', ?, 'completed', 'snap-demo', ?, ?, ?)
                """,
                (request_id, pid, exp_ids[exp_key], role, capability_hash, iso(days - 1), iso(days), index + 1),
            )
            conn.execute(
                """
                INSERT INTO review_sessions (id, request_id, declared_agent, independence, status, created_at)
                VALUES (?, ?, 'demo-reviewer', 'attested_agent_review', 'completed', ?)
                """,
                (session_id, request_id, iso(days)),
            )
            conn.execute(
                """
                INSERT INTO reviews (id, project_id, request_id, session_id, target_snapshot_id,
                                     target_type, target_id, role, verdict, synopsis, created_at, created_seq)
                VALUES (?, ?, ?, ?, 'snap-demo', 'experiment', ?, ?, ?, ?, ?, ?)
                """,
                (new_id(prefix="rev"), pid, request_id, session_id, exp_ids[exp_key], role,
                 verdict, synopsis, iso(days), index + 1),
            )

        for index, (key, title, status, days, claim_keys, exp_keys) in enumerate(REFLECTIONS):
            syn_id = new_id(prefix="syn")
            conn.execute(
                """
                INSERT INTO reflections (id, project_id, title, status, published_at, created_at, updated_at, created_seq)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (syn_id, pid, title, status,
                 iso(days - 1) if status == "published" else None,
                 iso(days), iso(days), index + 1),
            )
            for claim_key in claim_keys:
                conn.execute(
                    "INSERT INTO synthesis_claim_changes (synthesis_id, claim_id, op, created_at) VALUES (?, ?, 'update', ?)",
                    (syn_id, claim_ids[claim_key], iso(days)),
                )
            for exp_key in exp_keys:
                conn.execute(
                    "INSERT INTO synthesis_experiments (synthesis_id, experiment_id, created_at) VALUES (?, ?, ?)",
                    (syn_id, exp_ids[exp_key], iso(days)),
                )

        # Freshness = last event per target: align the seed events (written at
        # wall-clock "now" by the create tools) with the demo timeline.
        for key, _ck, _n, _i, _status, _conclusion, _created, touched in EXPERIMENTS:
            conn.execute("UPDATE events SET created_at = ? WHERE target_id = ?", (iso(touched), exp_ids[key]))
        for key, _s, _status, _conf, days in CLAIMS:
            touched = min(days, min(
                (e[7] for e in EXPERIMENTS if e[1] == key), default=days
            ))
            conn.execute("UPDATE events SET created_at = ? WHERE target_id = ?", (iso(touched), claim_ids[key]))

    # A couple of authored positions: the human dragged the two KV claims
    # together (position/adjacency as knowledge the agent perceives).
    app.research_map.sync(project_id=pid)
    app.research_map.pin(project_id=pid, entity_id=claim_ids["c_quant"], x=-1150.0, y=620.0)
    app.research_map.pin(project_id=pid, entity_id=claim_ids["c_kv"], x=-1150.0, y=280.0)

    state = app.research_map.state(project_id=pid)
    print(f"seeded {len(state['entities'])} map entities into project {pid}")
    return server


if __name__ == "__main__":
    port = int(os.environ.get("MAP_DEMO_PORT", "8799"))
    server = build()
    uvicorn.run(server.fastapi_app, host="127.0.0.1", port=port, log_level="warning")
