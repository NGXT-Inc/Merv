"""Application-owned derived experiment-figure projection.

Builds a graph document — typed nodes + edges — for one experiment from state
the backend already owns: the attempt chain, submitted artifacts (with
attempt indices), review verdicts, sandbox liveness, conclusion, and tested
claims. Nothing here is agent-authored; every node is derived and therefore
true by construction. A later phase merges an agent-authored overlay (arms,
decisions, metrics, lessons) into the same document shape.

Pure projection logic — no DB or backend calls. The Application query gathers the
inputs (experiment state, review snapshots, open review requests, sandbox
view) and hands them in.

Node `status` values are normalized for UI coloring:
  pending | active | done | failed | superseded | abandoned
except `review` nodes, whose status is the verdict (pass | needs_changes |
fail | open) and `claim` nodes, whose status is the claim status.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from ..research_core import EXPERIMENT_WORKFLOW

FIGURE_SCHEMA_VERSION = 2

# Artifact roles that feed an attempt vs. ones an attempt produces (legacy
# untyped roles remain readable on rows backfilled from the resource era).
UPSTREAM_ROLES = {"plan", "input", "code", "config", "model"}

# Per-attempt, per-direction cap on individual artifact nodes. Old sandbox syncs
# could attach hundreds of files to one attempt; past the cap the remainder
# rolls up into a single `artifact_group` node so the canvas stays readable.
ARTIFACT_FANOUT_CAP = 6

# Which artifacts survive the cap, most-load-bearing first.
_ROLE_PRIORITY = {"plan": 0, "report": 1, "result": 2, "model": 3, "input": 4, "code": 5, "config": 6, "note": 7}

_ACTIVE_ATTEMPT_STATUSES = (
    EXPERIMENT_WORKFLOW.effect_sources("result_submission")
    | EXPERIMENT_WORKFLOW.effect_destinations("result_submission")
)
_ATTEMPT_STATUS = {
    state.name: "active" if state.name in _ACTIVE_ATTEMPT_STATUSES else "pending"
    for state in EXPERIMENT_WORKFLOW.states
}
_ATTEMPT_STATUS.update(
    {
        EXPERIMENT_WORKFLOW.success_status: "done",
        **{
            status: "failed"
            for status in EXPERIMENT_WORKFLOW.effect_destinations("fail_tracking")
        },
        **{
            status: "abandoned"
            for status in EXPERIMENT_WORKFLOW.effect_destinations("stop_tracking")
        },
    }
)

_REVIEW_LABELS = {
    state.review.role: state.review.action_name.replace("_", " ").capitalize()
    for state in EXPERIMENT_WORKFLOW.states
    if state.review is not None
}
_REVIEW_LABELS.update({
    "human": "Human review",
    "automated_check": "Automated check",
})
_RESULT_SUBMISSION_TRANSITIONS = {
    transition.name
    for transition in EXPERIMENT_WORKFLOW.transitions
    if "result_submission" in transition.effects
}


def _humanize(value: str) -> str:
    return value.replace("_", " ")


def _review_order(review: dict[str, Any]) -> tuple[int, str, str]:
    """Chronological key. Reviews arrive newest-first; created_seq is the
    authoritative insertion order, with created_at and the id as tie-breakers
    so rows that predate the column still sort deterministically."""
    try:
        seq = int(review.get("created_seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    return (seq, str(review.get("created_at") or ""), str(review.get("id") or ""))


def _chain_edge(add_edge, source: str, source_verdict: str | None, target: str) -> None:
    """Link a review round to whatever preceded it.

    `source_verdict` is None when the source is the attempt or submission
    itself. A round that sent the work back earns the dashed revision arrow;
    one that merely came first gets a plain sequence arrow."""
    if source_verdict is None:
        add_edge(source, target, "reviewed_by")
    elif source_verdict in {"needs_changes", "fail"}:
        add_edge(source, target, "revised_to")
    else:
        add_edge(source, target, "then")


def _artifact_label(artifact: dict[str, Any]) -> str:
    title = (artifact.get("title") or "").strip()
    if title:
        return title
    return PurePosixPath(str(artifact.get("path") or artifact.get("id") or "artifact")).name


def build_experiment_figure(
    *,
    experiment: dict[str, Any],
    review_attempts: dict[str, int],
    open_review_requests: list[dict[str, Any]],
    sandbox: dict[str, Any] | None,
    sandbox_active: bool = False,
) -> dict[str, Any]:
    """Project one experiment's state into a figure graph.

    `review_attempts` maps review id -> attempt_index (resolved from review
    snapshots by the caller; 0 means unknown). `sandbox` is a sandbox row view
    or None when the experiment never had one; `sandbox_active` is the
    caller's liveness verdict (the sandbox module owns status vocabulary).
    """
    current_attempt = max(1, int(experiment.get("attempt_index") or 1))
    status = str(experiment.get("status") or EXPERIMENT_WORKFLOW.initial)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    def add_edge(source: str, target: str, edge_type: str) -> None:
        edges.append(
            {
                "id": f"{source}->{target}:{edge_type}",
                "from": source,
                "to": target,
                "type": edge_type,
            }
        )

    def clamp_attempt(value: Any) -> int:
        try:
            attempt = int(value)
        except (TypeError, ValueError):
            attempt = 0
        if attempt < 1 or attempt > current_attempt:
            return current_attempt
        return attempt

    # ---- attempt spine ----
    for k in range(1, current_attempt + 1):
        is_current = k == current_attempt
        nodes.append(
            {
                "id": f"attempt:{k}",
                "type": "attempt",
                "label": f"Attempt {k}",
                "sublabel": _humanize(status) if is_current else "superseded",
                "status": _ATTEMPT_STATUS.get(status, "pending") if is_current else "superseded",
                "group": f"attempt:{k}",
                "ref": {"kind": "experiment", "id": experiment.get("id")},
            }
        )
        if k > 1:
            add_edge(f"attempt:{k - 1}", f"attempt:{k}", "revised_to")

    # ---- submission attempts: the rounds inside an experiment attempt ----
    # Only result-submission rounds become nodes. Every forward transition seals a
    # composition, but a plan submission is already drawn by the attempt spine;
    # what has no home on the canvas is the report round, which is exactly the
    # thing a review return to running repeats without bumping the attempt.
    submissions = [
        row
        for row in experiment.get("submissions", [])
        if str(row.get("transition") or "") in _RESULT_SUBMISSION_TRANSITIONS
    ]
    submission_nodes: dict[str, str] = {}
    rounds_by_attempt: dict[int, list[dict[str, Any]]] = {}
    for row in submissions:
        rounds_by_attempt.setdefault(clamp_attempt(row.get("attempt_index")), []).append(row)
    for attempt, rounds in sorted(rounds_by_attempt.items()):
        # Chained, not fanned: round 2 follows round 1 so the report loop reads
        # left to right like the design loop, instead of stacking as siblings.
        previous = f"attempt:{attempt}"
        for index, row in enumerate(rounds, start=1):
            node_id = f"submission:{attempt}.{index}"
            submission_nodes[str(row.get("id"))] = node_id
            nodes.append(
                {
                    "id": node_id,
                    "type": "submission",
                    "label": f"Submission {attempt}.{index}",
                    "sublabel": "results submitted",
                    "status": "done",
                    "group": f"attempt:{attempt}",
                    "ref": {"kind": "submission", "id": row.get("id")},
                    "meta": {"attempt_index": attempt, "submission_index": index},
                }
            )
            add_edge(previous, node_id, "submitted" if index == 1 else "revised_to")
            previous = node_id

    # ---- artifacts, one node per (artifact, attempt) association ----
    # Bucket by (attempt, direction), keep the most load-bearing files under
    # the fan-out cap, and roll the rest into one expandable group node.
    # Superseded rows now survive their round (that is the history), so mark
    # anything the target no longer treats as current.
    current_ids = {
        str(res.get("id")) for res in experiment.get("current_attempt_artifacts", [])
    }
    buckets: dict[tuple[int, bool], list[dict[str, Any]]] = {}
    seen_assoc: set[tuple[str, int]] = set()
    for res in experiment.get("artifacts", []):
        attempt = clamp_attempt(res.get("attempt_index"))
        key = (str(res.get("id")), attempt)
        if key in seen_assoc:
            continue
        seen_assoc.add(key)
        role = str(res.get("role") or "other")
        buckets.setdefault((attempt, role in UPSTREAM_ROLES), []).append(res)

    for (attempt, upstream), bucket in sorted(buckets.items()):
        bucket.sort(
            key=lambda r: (
                _ROLE_PRIORITY.get(str(r.get("role") or "other"), 9),
                str(r.get("path") or ""),
            )
        )
        shown, overflow = bucket[:ARTIFACT_FANOUT_CAP], bucket[ARTIFACT_FANOUT_CAP:]
        for res in shown:
            role = str(res.get("role") or "other")
            node_id = f"artifact:{res.get('id')}:a{attempt}"
            superseded = (
                bool(current_ids) and str(res.get("id")) not in current_ids
            )
            nodes.append(
                {
                    "id": node_id,
                    "type": "artifact",
                    "label": _artifact_label(res),
                    "sublabel": f"{role} · superseded" if superseded else role,
                    "status": "superseded" if superseded else "none",
                    "group": f"attempt:{attempt}",
                    "ref": {"kind": "artifact", "id": res.get("id")},
                    "meta": {
                        "role": role,
                        "path": res.get("path"),
                        "superseded": superseded,
                    },
                }
            )
            if upstream:
                add_edge(node_id, f"attempt:{attempt}", "feeds")
            else:
                # A produced file hangs off the round that shipped it, so a
                # rejected round keeps its own report instead of every version
                # piling onto the attempt.
                source = submission_nodes.get(str(res.get("submission_id") or ""))
                add_edge(source or f"attempt:{attempt}", node_id, "produced")
        if overflow:
            roles = sorted({str(r.get("role") or "other") for r in overflow})
            node_id = f"artifact_group:a{attempt}:{'up' if upstream else 'down'}"
            nodes.append(
                {
                    "id": node_id,
                    "type": "artifact_group",
                    "label": f"{len(overflow)} more files",
                    "sublabel": " · ".join(roles),
                    "status": "none",
                    "group": f"attempt:{attempt}",
                    "ref": {"kind": "artifact_group", "id": None},
                    "meta": {
                        "count": len(overflow),
                        "roles": roles,
                        "artifact_ids": [str(r.get("id")) for r in overflow],
                    },
                }
            )
            if upstream:
                add_edge(node_id, f"attempt:{attempt}", "feeds")
            else:
                add_edge(f"attempt:{attempt}", node_id, "produced")

    # ---- submitted reviews, rooted on the round they graded ----
    # A review of a submission hangs off that submission; a design review (or
    # any review predating submissions) hangs off the attempt. Rounds sharing a
    # root chain in the order they happened rather than fanning out, which is
    # what gives the report loop a spine instead of a vertical pile.
    reviews_by_root: dict[tuple[str, int], list[dict[str, Any]]] = {}
    for review in experiment.get("reviews", []):
        attempt = clamp_attempt(review_attempts.get(str(review.get("id"))))
        root = submission_nodes.get(str(review.get("submission_id") or "")) or f"attempt:{attempt}"
        reviews_by_root.setdefault((root, attempt), []).append(review)

    tails: dict[str, tuple[str, str | None]] = {}
    for (root, attempt), rounds in sorted(reviews_by_root.items()):
        rounds.sort(key=_review_order)
        source, source_verdict = root, None
        for review in rounds:
            review_id = str(review.get("id"))
            verdict = str(review.get("verdict") or "")
            node_id = f"review:{review_id}"
            nodes.append(
                {
                    "id": node_id,
                    "type": "review",
                    "label": _REVIEW_LABELS.get(str(review.get("role")), "Review"),
                    "sublabel": _humanize(verdict),
                    "status": verdict or "open",
                    "group": f"attempt:{attempt}",
                    "ref": {"kind": "review", "id": review_id},
                    "meta": {
                        "role": review.get("role"),
                        "synopsis": review.get("synopsis") or "",
                        "notes": review.get("notes") or "",
                    },
                }
            )
            _chain_edge(add_edge, source, source_verdict, node_id)
            source, source_verdict = node_id, verdict
            # Only a rejection sent back to planning spawns the next attempt.
            # One sent back to running is another round of this same attempt,
            # already drawn by the submission chain.
            route = EXPERIMENT_WORKFLOW.return_route(
                str(review.get("return_to") or "")
            )
            if (
                verdict == "needs_changes"
                and attempt < current_attempt
                and (route is None or route.attempt == "new")
            ):
                add_edge(node_id, f"attempt:{attempt + 1}", "revised_to")
        tails[root] = (source, source_verdict)

    # ---- open review gates (requested/started, no verdict yet) ----
    for request in open_review_requests:
        node_id = f"review_request:{request.get('id')}"
        nodes.append(
            {
                "id": node_id,
                "type": "review",
                "label": _REVIEW_LABELS.get(str(request.get("role")), "Review"),
                "sublabel": "awaiting verdict",
                "status": "open",
                "group": f"attempt:{current_attempt}",
                "ref": {"kind": "review_request", "id": request.get("id")},
            }
        )
        # Land after the newest verdict on the round being reviewed, not back
        # on the attempt node.
        root = f"attempt:{current_attempt}"
        for submission in reversed(submissions):
            if clamp_attempt(submission.get("attempt_index")) == current_attempt:
                root = submission_nodes[str(submission.get("id"))]
                break
        source, source_verdict = tails.get(root, (root, None))
        _chain_edge(add_edge, source, source_verdict, node_id)
        tails[root] = (node_id, "")

    # ---- sandbox / execution ----
    if sandbox and str(sandbox.get("status") or "none") != "none":
        sandbox_status = str(sandbox.get("status"))
        nodes.append(
            {
                "id": "sandbox",
                "type": "sandbox",
                "label": "Sandbox",
                "sublabel": str(sandbox.get("gpu") or sandbox.get("instance_type") or sandbox_status),
                "status": "active" if sandbox_active else "done",
                "group": f"attempt:{current_attempt}",
                "ref": {"kind": "sandbox", "id": experiment.get("id")},
                "meta": {"sandbox_status": sandbox_status},
            }
        )
        add_edge(f"attempt:{current_attempt}", "sandbox", "ran_on")

    # ---- conclusion + tested claims ----
    conclusion = str(experiment.get("conclusion") or "").strip()
    claim_source = f"attempt:{current_attempt}"
    if conclusion:
        nodes.append(
            {
                "id": "conclusion",
                "type": "conclusion",
                "label": "Conclusion",
                "sublabel": conclusion,
                "status": "done",
                "group": f"attempt:{current_attempt}",
                "ref": {"kind": "experiment", "id": experiment.get("id")},
            }
        )
        add_edge(claim_source, "conclusion", "concludes")
        claim_source = "conclusion"
    for claim in experiment.get("tested_claims", []):
        node_id = f"claim:{claim.get('id')}"
        nodes.append(
            {
                "id": node_id,
                "type": "claim",
                "label": str(claim.get("statement") or claim.get("id")),
                "sublabel": _humanize(str(claim.get("status") or "")),
                "status": str(claim.get("status") or "active"),
                "ref": {"kind": "claim", "id": claim.get("id")},
            }
        )
        add_edge(claim_source, node_id, "tests")

    return {
        "schema_version": FIGURE_SCHEMA_VERSION,
        "source": "derived",
        "experiment_id": experiment.get("id"),
        "intent": experiment.get("intent") or "",
        "status": status,
        "attempt_index": current_attempt,
        "groups": [
            {"id": f"attempt:{k}", "label": f"Attempt {k}"} for k in range(1, current_attempt + 1)
        ],
        "nodes": nodes,
        "edges": edges,
    }
