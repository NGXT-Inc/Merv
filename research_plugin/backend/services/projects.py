"""Project memory service."""

from __future__ import annotations

from typing import Any

from ..sync_config import (
    SyncExclusionPolicy,
    config_from_json,
    config_to_json,
    load_sync_exclusions_file,
    normalize_sync_exclusions,
    policy_from_config,
)
from ..utils import NotFoundError, ValidationError
from ..utils import new_id
from ..state.store import StateStore, row_to_dict
from ..utils import now_iso


class ProjectService:
    """Owns project metadata."""

    def __init__(self, *, store: StateStore) -> None:
        self.store = store

    def create(
        self,
        *,
        name: str,
        summary: str = "",
        sync_exclusions: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not name.strip():
            raise ValidationError("name is required")
        sync_exclusions_json = (
            "" if sync_exclusions is None else self._sync_exclusions_json(sync_exclusions)
        )
        with self.store.transaction() as conn:
            project_id = new_id(prefix="proj")
            conn.execute(
                """
                INSERT INTO projects (id, name, summary, sync_exclusions_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    name.strip(),
                    summary.strip(),
                    sync_exclusions_json,
                    now_iso(),
                ),
            )
            self.store.record_event(
                conn=conn,
                project_id=project_id,
                event_type="project.created",
                target_type="project",
                target_id=project_id,
                payload={"name": name},
            )
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            return self._project_view(row=row)

    def update(
        self,
        *,
        project_id: str | None = None,
        name: str | None = None,
        summary: str | None = None,
        sync_exclusions: dict[str, Any] | None = None,
        reset_sync_exclusions: bool = False,
    ) -> dict[str, Any]:
        with self.store.transaction() as conn:
            project_id = self.store.require_project_id(conn=conn, project_id=project_id)
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"project not found: {project_id}")
            next_name = row["name"] if name is None else name.strip()
            next_summary = row["summary"] if summary is None else summary.strip()
            next_sync_exclusions_json = str(row["sync_exclusions_json"] or "")
            if reset_sync_exclusions:
                next_sync_exclusions_json = ""
            elif sync_exclusions is not None:
                next_sync_exclusions_json = self._sync_exclusions_json(sync_exclusions)
            conn.execute(
                """
                UPDATE projects
                SET name = ?, summary = ?, sync_exclusions_json = ?
                WHERE id = ?
                """,
                (
                    next_name,
                    next_summary,
                    next_sync_exclusions_json,
                    project_id,
                ),
            )
            self.store.record_event(
                conn=conn,
                project_id=project_id,
                event_type="project.updated",
                target_type="project",
                target_id=project_id,
                payload={
                    "name": next_name,
                    "summary": next_summary,
                    "sync_exclusions_source": (
                        "project" if next_sync_exclusions_json else "config_file"
                    ),
                },
            )
            updated = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            return self._project_view(row=updated)

    def get(self, *, project_id: str | None = None) -> dict[str, Any]:
        conn = self.store.connect()
        try:
            project_id = self.store.require_project_id(conn=conn, project_id=project_id)
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"project not found: {project_id}")
            return self._project_view(row=row)
        finally:
            conn.close()

    def list_projects(self) -> dict[str, Any]:
        conn = self.store.connect()
        try:
            rows = conn.execute("SELECT * FROM projects ORDER BY created_at").fetchall()
            return {"projects": [self._project_view(row=row) for row in rows]}
        finally:
            conn.close()

    def get_settings(self, *, project_id: str | None = None) -> dict[str, Any]:
        project = self.get(project_id=project_id)
        return self._settings_view(project=project)

    def update_settings(
        self,
        *,
        project_id: str | None = None,
        sync_exclusions: dict[str, Any] | None = None,
        reset_sync_exclusions: bool = False,
    ) -> dict[str, Any]:
        if sync_exclusions is None and not reset_sync_exclusions:
            raise ValidationError(
                "sync_exclusions or reset_sync_exclusions is required"
            )
        project = self.update(
            project_id=project_id,
            sync_exclusions=sync_exclusions,
            reset_sync_exclusions=reset_sync_exclusions,
        )
        return self._settings_view(project=project)

    def sync_exclusion_policy(self, project_id: str) -> SyncExclusionPolicy:
        conn = self.store.connect()
        try:
            row = conn.execute(
                "SELECT * FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if row is None:
                return SyncExclusionPolicy.defaults()
            return policy_from_config(
                self._resolved_sync_exclusions(row=row)["sync_exclusions"]
            )
        finally:
            conn.close()

    def _project_view(self, *, row) -> dict[str, Any]:
        data = row_to_dict(row=row) or {}
        resolved = self._resolved_sync_exclusions(row=row)
        data["sync_exclusions"] = resolved["sync_exclusions"]
        data["sync_exclusions_source"] = resolved["source"]
        data.pop("sync_exclusions_json", None)
        return data

    def _settings_view(self, *, project: dict[str, Any]) -> dict[str, Any]:
        return {
            "project_id": project["id"],
            "sync_exclusions": project["sync_exclusions"],
            "sync_exclusions_source": project["sync_exclusions_source"],
            "config_file": ".research_plugin/sync_exclusions.json",
        }

    def _resolved_sync_exclusions(self, *, row) -> dict[str, Any]:
        raw = str(row["sync_exclusions_json"] or "") if row is not None else ""
        if raw:
            try:
                return {
                    "sync_exclusions": config_from_json(raw),
                    "source": "project",
                }
            except Exception as exc:  # noqa: BLE001
                raise ValidationError(f"invalid stored sync exclusion config: {exc}") from exc
        try:
            file_config = load_sync_exclusions_file(repo_root=self.store.repo_root)
            if file_config is not None:
                return {"sync_exclusions": file_config, "source": "config_file"}
        except Exception:
            pass
        return {
            "sync_exclusions": normalize_sync_exclusions(None),
            "source": "default",
        }

    def _sync_exclusions_json(self, sync_exclusions: dict[str, Any]) -> str:
        try:
            return config_to_json(sync_exclusions)
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
