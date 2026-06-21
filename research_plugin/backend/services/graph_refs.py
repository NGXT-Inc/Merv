"""Best-effort project graph reference resolution."""

from __future__ import annotations

from typing import Any

from ..state.store import BaseStateStore


class GraphRefResolver:
    """Resolves graph node refs against control-plane records."""

    def __init__(self, *, store: BaseStateStore) -> None:
        self.store = store

    def resolve_index(
        self, *, project_id: str, graph: dict[str, Any] | None
    ) -> dict[str, Any]:
        refs = self._refs_from_graph(graph=graph)
        if not refs:
            return {}
        conn = self.store.connect()
        try:
            return {
                ref: self._resolve_one(conn=conn, project_id=project_id, ref=ref)
                for ref in refs
            }
        finally:
            conn.close()

    @staticmethod
    def _refs_from_graph(*, graph: dict[str, Any] | None) -> list[str]:
        refs: list[str] = []
        seen: set[str] = set()
        for node in (graph or {}).get("nodes") or []:
            if not isinstance(node, dict):
                continue
            node_refs = node.get("refs")
            if not isinstance(node_refs, list):
                continue
            for ref in node_refs:
                if isinstance(ref, str) and ref.strip() and ref not in seen:
                    seen.add(ref)
                    refs.append(ref)
        return refs

    def _resolve_one(self, *, conn, project_id: str, ref: str) -> dict[str, Any]:
        if ref.startswith("res_"):
            row = conn.execute(
                "SELECT id, path, kind, title, missing FROM resources"
                " WHERE id = ? AND project_id = ? AND deleted = 0",
                (ref, project_id),
            ).fetchone()
            if row:
                return self._resource_ref(row=row)
        elif ref.startswith("rev_"):
            row = conn.execute(
                "SELECT id, role, verdict, created_at FROM reviews"
                " WHERE id = ? AND project_id = ?",
                (ref, project_id),
            ).fetchone()
            if row:
                return {
                    "type": "review",
                    "resolved": True,
                    "review_id": row["id"],
                    "role": row["role"],
                    "verdict": row["verdict"],
                    "created_at": row["created_at"],
                }
        elif ref.startswith("claim_"):
            row = conn.execute(
                "SELECT id, statement, status FROM claims WHERE id = ? AND project_id = ?",
                (ref, project_id),
            ).fetchone()
            if row:
                return {
                    "type": "claim",
                    "resolved": True,
                    "claim_id": row["id"],
                    "statement": row["statement"],
                    "status": row["status"],
                }
        elif ref.startswith("exp_"):
            row = conn.execute(
                "SELECT id, intent, status FROM experiments WHERE id = ? AND project_id = ?",
                (ref, project_id),
            ).fetchone()
            if row:
                return {
                    "type": "experiment",
                    "resolved": True,
                    "experiment_id": row["id"],
                    "intent": row["intent"],
                    "status": row["status"],
                }
        elif ref.startswith("syn_"):
            row = conn.execute(
                "SELECT id, title, status, published_at FROM syntheses WHERE id = ? AND project_id = ?",
                (ref, project_id),
            ).fetchone()
            if row:
                return {
                    "type": "synthesis",
                    "resolved": True,
                    "synthesis_id": row["id"],
                    "title": row["title"],
                    "status": row["status"],
                    "published_at": row["published_at"],
                }
        else:
            row = conn.execute(
                "SELECT id, path, kind, title, missing FROM resources"
                " WHERE project_id = ? AND path = ? AND deleted = 0",
                (project_id, ref),
            ).fetchone()
            if row:
                return self._resource_ref(row=row)
            # Path refs resolve against registered resources only; the control
            # plane cannot probe local working-tree files.
            return {
                "type": "unknown",
                "resolved": False,
                "hint": "not a registered resource path; register the file to make this ref resolvable",
            }
        return {"type": "unknown", "resolved": False}

    @staticmethod
    def _resource_ref(*, row) -> dict[str, Any]:
        return {
            "type": "resource",
            "resolved": True,
            "resource_id": row["id"],
            "path": row["path"],
            "kind": row["kind"],
            "title": row["title"],
            "missing": bool(row["missing"]),
        }
