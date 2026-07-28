"""Bounded project facts for the canonical agent orientation packet."""

from __future__ import annotations

from contextlib import closing
from typing import Any

from ..kernel.state.store import BaseStateStore, row_to_dict, rows_to_dicts


Record = dict[str, Any]


class ProjectContextFactsReader:
    """Read only the records needed to compose project-level agent context.

    Artifact bytes are deliberately outside this reader.  The application
    selects the small set of meaningful artifact roles, then asks Artifacts
    for summaries of only those versions.
    """

    def __init__(self, *, store: BaseStateStore) -> None:
        self.store = store

    def read(self, *, project_id: str | None = None) -> Record:
        with closing(self.store.connect()) as conn:
            project_id = self.store.require_project_id(
                conn=conn, project_id=project_id
            )
            project = row_to_dict(
                row=conn.execute(
                    """
                    SELECT id, name, summary
                    FROM projects
                    WHERE id = ?
                    """,
                    (project_id,),
                ).fetchone()
            ) or {}
            claims = rows_to_dicts(
                rows=conn.execute(
                    """
                    SELECT id, statement, scope, status, confidence
                    FROM claims
                    WHERE project_id = ?
                    ORDER BY created_at, id
                    """,
                    (project_id,),
                ).fetchall()
            )
            experiments = rows_to_dicts(
                rows=conn.execute(
                    """
                    SELECT id, name, intent, status, attempt_index, conclusion,
                           created_at, updated_at
                    FROM experiments
                    WHERE project_id = ?
                    ORDER BY created_at, id
                    """,
                    (project_id,),
                ).fetchall()
            )
            claim_links = rows_to_dicts(
                rows=conn.execute(
                    """
                    SELECT ec.experiment_id, ec.claim_id
                    FROM experiment_claims ec
                    JOIN experiments e ON e.id = ec.experiment_id
                    WHERE e.project_id = ?
                    ORDER BY e.created_at, e.id, ec.claim_id
                    """,
                    (project_id,),
                ).fetchall()
            )
            by_experiment: dict[str, list[str]] = {}
            for link in claim_links:
                by_experiment.setdefault(
                    str(link["experiment_id"]), []
                ).append(str(link["claim_id"]))
            for experiment in experiments:
                experiment["tested_claim_ids"] = by_experiment.get(
                    str(experiment["id"]), []
                )

            latest_published = row_to_dict(
                row=conn.execute(
                    """
                    SELECT id, title, status, attempt_index, published_at,
                           updated_at
                    FROM reflections
                    WHERE project_id = ? AND status = 'published'
                    ORDER BY published_at DESC, created_seq DESC
                    LIMIT 1
                    """,
                    (project_id,),
                ).fetchone()
            )
            open_wave = row_to_dict(
                row=conn.execute(
                    """
                    SELECT id, title, status, attempt_index, updated_at
                    FROM reflections
                    WHERE project_id = ?
                      AND status NOT IN ('published', 'abandoned')
                    ORDER BY created_seq DESC
                    LIMIT 1
                    """,
                    (project_id,),
                ).fetchone()
            )
            literature_summary = row_to_dict(
                row=conn.execute(
                    """
                    SELECT id, tldr, body, updated_at
                    FROM litreview_sections
                    WHERE project_id = ? AND kind = 'summary'
                    """,
                    (project_id,),
                ).fetchone()
            )
            paper_count_row = conn.execute(
                "SELECT COUNT(*) AS n FROM papers WHERE project_id = ?",
                (project_id,),
            ).fetchone()

        return {
            "project": project,
            "claims": claims,
            "experiments": experiments,
            "latest_published_reflection": latest_published,
            "open_reflection": open_wave,
            "literature_summary": literature_summary,
            "paper_count": int(paper_count_row["n"]) if paper_count_row else 0,
        }


__all__ = ["ProjectContextFactsReader"]
