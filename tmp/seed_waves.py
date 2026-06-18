"""Seed a repo+db with two published reflection waves for UI verification.

Run from research_plugin/ with its venv. Produces a single-project state the
dev HTTP daemon can serve via --repo/--store. Two waves with DISTINCT pinned
graphs prove the per-wave (faithful-history) endpoint: wave 2 overwrites the
living project/logic_graph.json, yet wave 1's graph must still render its own
bytes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import ResearchPluginApp
from backend.http_api import create_fastapi_app
from backend.execution.backends.fake import FakeSandboxBackend
from tests.fakes import FakeRsyncSyncer

REPO = Path(sys.argv[1]).resolve()
STORE = REPO / ".research_plugin" / "state.sqlite"
REPO.mkdir(parents=True, exist_ok=True)

app = ResearchPluginApp(
    repo_root=REPO,
    db_path=STORE,
    execution_backend=FakeSandboxBackend(),
    rsync_syncer=FakeRsyncSyncer(sync_pulled=1, sync_stdout="metrics.json\n"),
)
client = TestClient(create_fastapi_app(app))


def req(method, path, body=None):
    r = client.request(method, path, json=body)
    assert r.status_code < 400, f"{method} {path} -> {r.status_code}: {r.text}"
    return r.json()


pid = req("POST", "/api/projects", {"name": "Optimizer Study", "summary": "Does the LR schedule dominate batch effects?"})["id"]

LENSES = [
    {"id": "amplify"},
    {"id": "avoid"},
    {"id": "entropy"},
    {"id": "rigor", "charter": "Are the measurements sound — seeds, baselines, error bars?",
     "why_distinct": "Judges HOW we measured, not WHAT we found (amplify's job)."},
    {"id": "cost", "charter": "Compute and wall-clock spent per bit of information gained.",
     "why_distinct": "Prices the exploration; the others ignore budget."},
]

REFLECTIONS = {
    "amplify": "# Amplify what works\n\nThe LR-schedule win is **supported** by `exp_a` and `exp_c` "
               "(accuracy +2.1pt, outside seed noise) — invest more here. The batch-size claim is *contested* "
               "— `exp_b` shows no effect once the schedule is fixed.\n\n- established: schedule > batch effects\n"
               "- do more of: schedule sweeps at new scales\n",
    "avoid": "# Avoid what failed\n\n| direction tested | setting | what happened | why it failed |\n"
             "|---|---|---|---|\n| optimizer swap (Adam→Lion) | exp_b a2 | no gain, +noise | LR floor coupling |\n"
             "| longer warmup | exp_a a3 | flat | schedule already saturates |\n",
    "entropy": "# Entropy & weird bets\n\nHigh-variance ideas the other lenses would dismiss: train with a "
               "**deliberately mismatched tokenizer** to probe robustness; try a **schedule that anneals "
               "upward** late in training. Cheap to test, surprising if either moves the needle.\n",
    "rigor": "# Methodological rigor\n\nSeeds: only 1 seed on `exp_c` — the +2.1pt should be re-run at n≥3. "
             "Baselines are consistent. Error bars reported except on the scale extrapolation.\n",
    "cost": "# Cost / compute efficiency\n\nThe schedule sweep cost ~40 GPU-h for the decisive result; the "
            "optimizer-swap dead end burned ~70 GPU-h across three attempts. Future swaps should gate on a "
            "cheap LR-floor probe first.\n",
}

GRAPHS = {
    1: {"version": 1, "title": "Project logic — wave 1",
        "nodes": [
            {"id": "anchor", "kind": "established", "label": "LR schedule dominates batch effects", "refs": []},
            {"id": "wall", "kind": "dead_end_pattern", "label": "Optimizer swaps fail on LR-floor coupling", "refs": []},
            {"id": "open", "kind": "open_question", "label": "Does the anchor hold at 10x scale?", "refs": []},
        ],
        "edges": [{"from": "anchor", "to": "open", "label": "raises"}, {"from": "wall", "to": "open", "label": "constrains"}]},
    2: {"version": 1, "title": "Project logic — wave 2",
        "nodes": [
            {"id": "anchor", "kind": "established", "label": "LR schedule win replicated at n=3", "refs": []},
            {"id": "scale", "kind": "established", "label": "Anchor holds to 4x scale", "refs": []},
            {"id": "wall", "kind": "dead_end_pattern", "label": "Optimizer swaps: 3 failures, same cause", "refs": []},
            {"id": "mix", "kind": "open_question", "label": "Does it survive a shifted data mixture?", "refs": []},
        ],
        "edges": [{"from": "anchor", "to": "scale", "label": "extends"}, {"from": "scale", "to": "mix", "label": "raises"}]},
}

PROPOSALS = {
    1: "# What's next — wave 1\n\n## P1 · Scale check on the LR anchor\nHypothesis: the schedule win survives 10x params.\nbuilds_on: exp_a, exp_c\n\n## P2 · Re-run exp_c at n≥3\nThe decisive result rests on one seed.\n",
    2: "# What's next — wave 2\n\n## P1 · Shifted-mixture robustness\nHypothesis: the anchor degrades under a 30% data-mixture shift.\nbuilds_on: exp_a\n\n## P2 · Optimizer swap, 4th attempt — differs from the ledger\nRe-tune the LR floor jointly this time.\n",
}


SYNTH_DOCS = {
    1: "# Synthesis — wave 1\n\n## Summary\nThe LR schedule is the project's load-bearing result; "
       "batch-size effects wash out once it is fixed. Optimizer swaps are a repeated dead end.\n\n"
       "## Critical reading\nThe 10x-scale claim is an extrapolation with no experiment yet, and the "
       "decisive +2.1pt rests on a single seed — both are leaned on harder than the evidence supports.\n\n"
       "## Decision / future directions\nRun a scale check and re-run the decisive result at n≥3.\n",
    2: "# Synthesis — wave 2\n\n## Summary\nThe schedule win replicated at n=3 and holds to 4x scale. "
       "The frontier moves to data-mixture robustness.\n\n"
       "## Critical reading\nScale evidence stops at 4x; the 10x claim is still open. Robustness to a "
       "shifted eval distribution remains entirely untested despite being a stated project goal.\n\n"
       "## Decision / future directions\nProbe a shifted data mixture; one more optimizer attempt that "
       "differs from the ledger.\n",
}

CHANGE_SPECS = {
    1: {"version": 1,
        "claim_changes": [{"op": "create", "key": "claim_scale_w1",
                           "statement": "The LR-schedule win survives a 10x parameter increase.",
                           "confidence": "low", "rationale": "Extrapolation flagged by the rigor lens."}],
        "decision": {"type": "create_experiments", "experiments": [
            {"key": "scale_w1_a", "name": "scale-check-10x", "intent": "Re-run the schedule sweep at 10x params.",
             "tested_claim_refs": ["claim_scale_w1"], "parallelism": "Independent scale axis."},
            {"key": "scale_w1_b", "name": "replicate-n3", "intent": "Re-run the decisive result at n=3 seeds.",
             "tested_claim_refs": ["claim_scale_w1"], "parallelism": "Independent replication axis."}]}},
    2: {"version": 1,
        "claim_changes": [{"op": "create", "key": "claim_mix_w2",
                           "statement": "The schedule win degrades under a 30% data-mixture shift.",
                           "confidence": "low", "rationale": "Coverage lens flagged mixture as cold."}],
        "decision": {"type": "create_experiments", "experiments": [
            {"key": "mix_w2_a", "name": "mixture-shift-30", "intent": "Evaluate under a 30% mixture shift.",
             "tested_claim_refs": ["claim_mix_w2"], "parallelism": "Independent robustness axis."},
            {"key": "mix_w2_b", "name": "lion-floor-joint", "intent": "Optimizer swap retuning the LR floor jointly.",
             "tested_claim_refs": ["claim_mix_w2"], "parallelism": "Independent optimizer axis."}]}},
}


def run_wave(n: int) -> str:
    syn_id = app.call_tool("synthesis.create", {"project_id": pid, "title": f"Wave {n}", "lenses": LENSES})["id"]
    for lens, text in REFLECTIONS.items():
        rel = f"syntheses/{syn_id}/reflections/{lens}.md"
        p = REPO / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        res = req("POST", f"/api/projects/{pid}/resources", {"path": rel})
        req("POST", f"/api/projects/{pid}/resources/{res['id']}/associate",
            {"target_type": "synthesis", "target_id": syn_id, "role": "reflection_lens_doc"})
    app.call_tool("synthesis.transition", {"project_id": pid, "synthesis_id": syn_id, "transition": "submit_reflections"})

    (REPO / "project").mkdir(exist_ok=True)
    (REPO / "project/logic_graph.json").write_text(json.dumps(GRAPHS[n]))
    (REPO / "project/reflection.md").write_text(SYNTH_DOCS[n])
    (REPO / "project/change_spec.json").write_text(json.dumps(CHANGE_SPECS[n]))
    (REPO / "project/proposals.md").write_text(PROPOSALS[n])
    for rel, role in (
        ("project/logic_graph.json", "graph"),
        ("project/reflection.md", "reflection_doc"),
        ("project/change_spec.json", "change_spec"),
        ("project/proposals.md", "proposals"),
    ):
        res = req("POST", f"/api/projects/{pid}/resources", {"path": rel})
        req("POST", f"/api/projects/{pid}/resources/{res['id']}/associate",
            {"target_type": "synthesis", "target_id": syn_id, "role": role})
    app.call_tool("synthesis.transition", {"project_id": pid, "synthesis_id": syn_id, "transition": "submit_synthesis"})

    reqd = req("POST", f"/api/projects/{pid}/reviews/request",
               {"target_type": "synthesis", "target_id": syn_id, "role": "synthesis_reviewer"})
    sess = req("POST", f"/api/projects/{pid}/reviews/start",
               {"review_request_id": reqd["review_request_id"], "reviewer_capability": reqd["reviewer_capability"],
                "caller_session_id": f"seed-reviewer-{n}"})
    req("POST", f"/api/projects/{pid}/reviews/submit",
        {"review_session_id": sess["review_session_id"], "verdict": "pass",
         "notes": f"Wave {n} reconciles with the corpus; proposals are grounded.",
         "findings": [] if n == 2 else [
             {"severity": "low", "issue": "Scale extrapolation is leaned on harder than its evidence.",
              "evidence": "node 'open'", "recommended_change": "Mark it open until exp at scale lands."}]})
    app.call_tool("synthesis.transition", {"project_id": pid, "synthesis_id": syn_id, "transition": "publish"})
    return syn_id


w1 = run_wave(1)
w2 = run_wave(2)
print(json.dumps({"project_id": pid, "wave1": w1, "wave2": w2, "store": str(STORE)}))
