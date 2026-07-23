"""Backend-mandated typed artifacts uploaded directly to the brain.

One entity: an artifact = a typed object submitted against a workflow target.
``submit`` validates legality and mints a pending row with a one-time upload
token; the transport PUT hands the raw bytes to ``complete_upload``, which
enforces the role byte cap, pins the bytes in the blob store, supersedes any
previous artifact in the same slot, and (for gated markdown) mints follow-up
figure-upload tokens. No path identity, no versions, no fingerprints — ``path``
is a trust-based provenance label.
"""

from __future__ import annotations

from contextlib import closing
import json
import mimetypes
import secrets
from typing import Any

from merv.shared.artifact_roles import (
    REFLECTION_LENS_DOC_ROLE,
    SYSTEM_CREATED_BY,
    artifact_byte_cap,
)
from merv.shared.markdown_images import (
    MARKDOWN_FIGURE_MAX_BYTES,
    MARKDOWN_FIGURE_ROLES,
    markdown_image_links,
)

from ..kernel.ports.blob_store import EvidenceBlobStore
from ..kernel.state.store import (
    BaseStateStore,
    Connection,
    Row,
    next_created_seq,
    row_to_dict,
    rows_to_dicts,
)
from ..kernel.utils import (
    NotFoundError,
    ValidationError,
    WorkflowError,
    iso_after,
    new_id,
    now_iso,
)
from .association_policy import validate_artifact_association
from .ports import (
    AssociatedEvidence,
    AssociationTargetResolver,
    SubmittedDocument,
    SubmittedEvidence,
)

UPLOAD_TOKEN_TTL_SECONDS = 15 * 60
_LOCAL_API_BASE = "http://127.0.0.1:8787"
_CONTENT_TYPES = {".md": "text/markdown", ".json": "application/json"}

_ARTIFACT_LIST_FIELDS = (
    "id", "target_type", "target_id", "role", "attempt_index", "lens_id",
    "path", "title", "size_bytes", "content_type", "status", "created_by",
    "created_at", "updated_at",
)


def _content_type_for(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    suffix = ("." + name.rsplit(".", 1)[-1]).lower() if "." in name else ""
    return (
        _CONTENT_TYPES.get(suffix)
        or mimetypes.guess_type(name)[0]
        or "application/octet-stream"
    )


def upload_command(*, base_url: str, path: str, token: str, kind: str = "u") -> str:
    """The ready-to-run one-liner the agent executes verbatim."""
    base = (base_url or _LOCAL_API_BASE).rstrip("/")
    safe_path = path if " " not in path else f'"{path}"'
    return f"curl -sf -T {safe_path} '{base}/api/artifacts/{kind}/{token}'"


def _evidence(row: Row) -> AssociatedEvidence:
    return AssociatedEvidence(
        artifact_id=str(row["id"]),
        project_id=str(row["project_id"]),
        role=str(row["role"]),
        attempt_index=int(row["attempt_index"]),
        lens_id=str(row["lens_id"] or ""),
        path=str(row["path"] or ""),
        title=str(row["title"] or ""),
        content_sha256=str(row["content_sha256"] or ""),
        size_bytes=int(row["size_bytes"] or 0),
        content_type=str(row["content_type"] or ""),
        created_by=str(row["created_by"] or ""),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        order=int(row["created_seq"] or 0),
    )


class ArtifactSubmissionService:
    """Owns the artifact rows, upload tokens, and submitted-byte reads."""

    def __init__(
        self,
        *,
        store: BaseStateStore,
        association_targets: AssociationTargetResolver,
        blobs: EvidenceBlobStore | None = None,
    ) -> None:
        self.store = store
        self.blobs = blobs
        self.association_targets = association_targets

    # ---- agent flow ----

    def submit(
        self,
        *,
        target_type: str,
        target_id: str,
        role: str,
        path: str,
        lens_id: str = "",
        title: str = "",
        project_id: str | None = None,
        created_by: str = "agent",
        base_url: str = "",
    ) -> dict[str, Any]:
        """Validate legality, create a pending artifact, return the upload line."""
        validate_artifact_association(target_type=target_type, role=role)
        if role == REFLECTION_LENS_DOC_ROLE and not lens_id:
            raise ValidationError(
                "lens_id is required for reflection_lens_doc artifacts — pass "
                "the roster lens this reflection covers"
            )
        if lens_id and role != REFLECTION_LENS_DOC_ROLE:
            raise ValidationError("lens_id only applies to reflection_lens_doc artifacts")
        if not str(path).strip():
            raise ValidationError("path is required (the local file you wrote)")
        if self.blobs is None:
            raise WorkflowError("artifact submission requires a configured blob store")
        rel_path = str(path).strip().replace("\\", "/").lstrip("/")
        self._sweep_expired()
        with self.store.transaction() as conn:
            project_id = self.store.require_project_id(conn=conn, project_id=project_id)
            target = self.association_targets.resolve(
                target_type=target_type, target_id=target_id
            )
            if target.project_id is not None and target.project_id != project_id:
                raise NotFoundError(
                    f"{target_type} not found in project {project_id}: {target_id}"
                )
            artifact_id = new_id(prefix="art")
            token = secrets.token_urlsafe(24)
            now = now_iso()
            conn.execute(
                """
                INSERT INTO artifacts (
                  id, project_id, target_type, target_id, role, attempt_index,
                  lens_id, path, title, status, upload_token, expires_at,
                  created_by, created_at, updated_at, created_seq
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (
                    artifact_id, project_id, target_type, target_id, role,
                    target.attempt_index, lens_id, rel_path, title, token,
                    iso_after(seconds=UPLOAD_TOKEN_TTL_SECONDS), created_by,
                    now, now, next_created_seq(conn=conn, table="artifacts"),
                ),
            )
        return {
            "artifact_id": artifact_id,
            "run": upload_command(base_url=base_url, path=rel_path, token=token),
        }

    def complete_upload(self, *, token: str, data: bytes) -> dict[str, Any]:
        """Pin the uploaded bytes: cap, sha, blob, flip complete, supersede."""
        if self.blobs is None:
            raise WorkflowError("artifact submission requires a configured blob store")
        self._sweep_expired()
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE upload_token = ? AND status = 'pending'",
                (token,),
            ).fetchone()
            if row is None:
                raise NotFoundError(
                    "unknown, used, or expired upload token — call artifact.submit again"
                )
            role, path = str(row["role"]), str(row["path"])
            cap = artifact_byte_cap(role)
            if cap is not None and len(data) > cap:
                raise ValidationError(
                    f"{path} is {len(data)} bytes; the maximum for a role-{role!r} "
                    f"artifact is {cap} bytes — slim the file (move raw "
                    "data/outputs elsewhere and reference them) and resubmit",
                    details={"role": role, "size_bytes": len(data), "max_bytes": cap},
                )
            project_id = str(row["project_id"])
            content_type = _content_type_for(path)
            sha = self.blobs.put(
                namespace=project_id, data=data, content_type=content_type
            )
            self._supersede_slot(conn=conn, row=row)
            conn.execute(
                """
                UPDATE artifacts
                SET status = 'complete', upload_token = '', expires_at = NULL,
                    content_sha256 = ?, size_bytes = ?, content_type = ?, updated_at = ?
                WHERE id = ?
                """,
                (sha, len(data), content_type, now_iso(), row["id"]),
            )
            self.store.record_event(
                conn=conn,
                project_id=project_id,
                event_type="artifact.submitted",
                target_type=str(row["target_type"]),
                target_id=str(row["target_id"]),
                payload={
                    "artifact_id": str(row["id"]),
                    "role": role,
                    "path": path,
                    "attempt_index": int(row["attempt_index"]),
                },
            )
            figures = self._mint_figure_tokens(conn=conn, row=row, data=data)
        return {
            "artifact_id": str(row["id"]),
            "role": role,
            "path": path,
            "sha256": sha,
            "size_bytes": len(data),
            "figures": figures,
        }

    def complete_figure_upload(self, *, token: str, data: bytes) -> dict[str, Any]:
        if self.blobs is None:
            raise WorkflowError("artifact submission requires a configured blob store")
        self._sweep_expired()
        with self.store.transaction() as conn:
            row = conn.execute(
                """
                SELECT f.*, a.project_id
                FROM artifact_figures f JOIN artifacts a ON a.id = f.artifact_id
                WHERE f.upload_token = ? AND f.status = 'pending'
                """,
                (token,),
            ).fetchone()
            if row is None:
                raise NotFoundError(
                    "unknown, used, or expired figure token — resubmit the "
                    "document to mint fresh figure uploads"
                )
            link = str(row["link_path"])
            if len(data) > MARKDOWN_FIGURE_MAX_BYTES:
                raise ValidationError(
                    f"figure {link!r} is {len(data)} bytes; the maximum is "
                    f"{MARKDOWN_FIGURE_MAX_BYTES} bytes",
                    details={"size_bytes": len(data), "max_bytes": MARKDOWN_FIGURE_MAX_BYTES},
                )
            sha = self.blobs.put(
                namespace=str(row["project_id"]),
                data=data,
                content_type=_content_type_for(link),
            )
            conn.execute(
                """
                UPDATE artifact_figures
                SET status = 'complete', upload_token = '', expires_at = NULL,
                    content_sha256 = ?, size_bytes = ?
                WHERE id = ?
                """,
                (sha, len(data), row["id"]),
            )
        return {
            "artifact_id": str(row["artifact_id"]),
            "link_path": link,
            "sha256": sha,
            "size_bytes": len(data),
        }

    # ---- reads ----

    def find(
        self,
        *,
        project_id: str | None = None,
        artifact_id: str = "",
        target_type: str = "",
        target_id: str = "",
        role: str = "",
    ) -> dict[str, Any]:
        """Compact artifact listing by target or project, or one by id."""
        with closing(self.store.connect()) as conn:
            project_id = self.store.require_project_id(conn=conn, project_id=project_id)
            if artifact_id:
                return {"artifact": self._require(conn=conn, project_id=project_id, artifact_id=artifact_id)}
            where = ["project_id = ?", "status = 'complete'"]
            params: list[Any] = [project_id]
            for column, value in (
                ("target_type", target_type), ("target_id", target_id), ("role", role)
            ):
                if value:
                    where.append(f"{column} = ?")
                    params.append(value)
            rows = conn.execute(
                f"""
                SELECT * FROM artifacts WHERE {' AND '.join(where)}
                ORDER BY target_type, target_id, attempt_index, role, path
                """,
                params,
            ).fetchall()
        artifacts = [
            {key: record.get(key) for key in _ARTIFACT_LIST_FIELDS}
            for record in rows_to_dicts(rows=rows)
        ]
        return {"artifacts": artifacts, "count": len(artifacts)}

    def resolve(
        self, *, artifact_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        with closing(self.store.connect()) as conn:
            project_id = self.store.require_project_id(conn=conn, project_id=project_id)
            return self._require(conn=conn, project_id=project_id, artifact_id=artifact_id)

    def artifact_content(
        self, *, artifact_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """Submitted text for one artifact, shaped for UI display."""
        artifact = self.resolve(artifact_id=artifact_id, project_id=project_id)
        text = self.submitted_text_for_artifact(artifact_id=str(artifact["id"]))
        return {
            "artifact": artifact,
            "path": artifact.get("path"),
            "content": text,
            "text": text,
            "available": text is not None,
            "source": "submitted" if text is not None else "unavailable",
        }

    def artifact_file(
        self, *, artifact_id: str, project_id: str | None = None
    ) -> tuple[bytes, str, str]:
        """Raw submitted bytes, content type, and filename for one artifact."""
        artifact = self.resolve(artifact_id=artifact_id, project_id=project_id)
        if self.blobs is None or artifact.get("status") != "complete":
            raise NotFoundError(f"artifact has no submitted content: {artifact_id}")
        data = self.blobs.get(
            namespace=str(artifact["project_id"]),
            sha256=str(artifact["content_sha256"]),
        )
        path = str(artifact.get("path") or artifact_id)
        return (
            data,
            str(artifact.get("content_type") or "application/octet-stream"),
            path.rsplit("/", 1)[-1],
        )

    def figure_bytes(
        self, *, artifact_id: str, link_path: str, project_id: str | None = None
    ) -> bytes | None:
        """Best-effort submitted figure bytes for a markdown image link."""
        if self.blobs is None:
            return None
        with closing(self.store.connect()) as conn:
            project_id = self.store.require_project_id(conn=conn, project_id=project_id)
            row = conn.execute(
                """
                SELECT f.content_sha256 FROM artifact_figures f
                JOIN artifacts a ON a.id = f.artifact_id
                WHERE f.artifact_id = ? AND f.link_path = ?
                  AND f.status = 'complete' AND a.project_id = ?
                """,
                (artifact_id, link_path, project_id),
            ).fetchone()
        if row is None:
            return None
        try:
            return self.blobs.get(
                namespace=str(project_id), sha256=str(row["content_sha256"])
            )
        except NotFoundError:
            return None

    def submitted_text_for_artifact(self, *, artifact_id: str | None) -> str | None:
        """Best-effort submitted text for one artifact, decoded for display."""
        if not artifact_id or self.blobs is None:
            return None
        with closing(self.store.connect()) as conn:
            row = conn.execute(
                "SELECT project_id, content_sha256 FROM artifacts "
                "WHERE id = ? AND status = 'complete'",
                (str(artifact_id),),
            ).fetchone()
        if row is None:
            return None
        try:
            data = self.blobs.get(
                namespace=str(row["project_id"]), sha256=str(row["content_sha256"])
            )
        except NotFoundError:
            return None
        return data.decode("utf-8", errors="replace")

    # ---- EvidenceReader port ----

    def artifacts_for_target(
        self, *, target_type: str, target_id: str
    ) -> tuple[AssociatedEvidence, ...]:
        return self.artifacts_for_targets(
            target_type=target_type, target_ids=(target_id,)
        )[target_id]

    def artifacts_for_targets(
        self, *, target_type: str, target_ids: tuple[str, ...]
    ) -> dict[str, tuple[AssociatedEvidence, ...]]:
        ids = list(dict.fromkeys(str(target_id) for target_id in target_ids))
        grouped: dict[str, list[AssociatedEvidence]] = {tid: [] for tid in ids}
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM artifacts
                WHERE status = 'complete' AND target_type = ?
                  AND target_id IN ({placeholders})
                ORDER BY target_id, attempt_index, role, path
                """,
                (target_type, *ids),
            ).fetchall()
        for row in rows:
            grouped[str(row["target_id"])].append(_evidence(row))
        return {tid: tuple(items) for tid, items in grouped.items()}

    def submitted_document(
        self, *, artifact_id: str | None, what: str
    ) -> SubmittedDocument:
        """Strict submitted text and figure membership for one artifact."""
        if self.blobs is None:
            raise WorkflowError(
                f"{what}: no blob store is configured; gated artifacts cannot be linted"
            )
        if not artifact_id:
            raise WorkflowError(
                f"{what} has no submitted artifact — submit it with artifact.submit"
            )
        with closing(self.store.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE id = ? AND status = 'complete'",
                (str(artifact_id),),
            ).fetchone()
            if row is None:
                raise WorkflowError(f"{what}: artifact not found: {artifact_id}")
            path = str(row["path"] or "")
            try:
                data = self.blobs.get(
                    namespace=str(row["project_id"]),
                    sha256=str(row["content_sha256"]),
                )
            except NotFoundError as exc:
                raise WorkflowError(
                    f"{what} ({path}) has no submitted content — resubmit it "
                    "with artifact.submit"
                ) from exc
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise WorkflowError(f"{what} ({path}) is not valid UTF-8 text") from exc
            figure_links = tuple(
                str(figure["link_path"])
                for figure in conn.execute(
                    """
                    SELECT link_path FROM artifact_figures
                    WHERE artifact_id = ? AND status = 'complete'
                    ORDER BY link_path
                    """,
                    (str(artifact_id),),
                ).fetchall()
            )
        return SubmittedDocument(
            text=text,
            artifact_id=str(artifact_id),
            path=path,
            role=str(row["role"]),
            figure_links=figure_links,
        )

    def submitted_evidence(
        self,
        *,
        target_type: str,
        target_id: str,
        attempt_index: int,
        roles: tuple[str, ...],
    ) -> tuple[SubmittedEvidence, ...]:
        """All current-attempt submitted text as best-effort facts."""
        if self.blobs is None or not roles:
            return ()
        role_slots = ", ".join("?" for _role in roles)
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM artifacts
                WHERE status = 'complete' AND target_type = ? AND target_id = ?
                  AND attempt_index = ? AND role IN ({role_slots})
                ORDER BY created_seq
                """,
                (target_type, target_id, int(attempt_index), *roles),
            ).fetchall()
        result: list[SubmittedEvidence] = []
        for row in rows:
            content = None
            try:
                data = self.blobs.get(
                    namespace=str(row["project_id"]),
                    sha256=str(row["content_sha256"]),
                )
                content = data.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - reviewer hydration is best-effort
                pass
            result.append(
                SubmittedEvidence(
                    role=str(row["role"]),
                    path=str(row["path"] or ""),
                    artifact_id=str(row["id"]),
                    order=int(row["created_seq"] or 0),
                    content=content,
                )
            )
        return tuple(result)

    # ---- system + exhibit ----

    def pin_system_artifact(
        self,
        *,
        path: str,
        target_type: str,
        target_id: str,
        role: str,
        content_bytes: bytes,
        content_type: str = "application/json",
        title: str = "",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a complete SYSTEM-authored artifact from in-memory bytes.

        Deliberately bypasses the agent role vocabulary: the roles the system
        pins (e.g. 'exhibit') are exactly the ones agents must not author."""
        if self.blobs is None:
            raise WorkflowError("system artifacts require a configured blob store")
        rel_path = str(path).strip().replace("\\", "/").lstrip("/")
        with self.store.transaction() as conn:
            project_id = self.store.require_project_id(conn=conn, project_id=project_id)
            target = self.association_targets.resolve(
                target_type=target_type, target_id=target_id
            )
            if target.project_id is not None and target.project_id != project_id:
                raise NotFoundError(
                    f"{target_type} not found in project {project_id}: {target_id}"
                )
            sha = self.blobs.put(
                namespace=project_id, data=content_bytes, content_type=content_type
            )
            artifact_id = new_id(prefix="art")
            now = now_iso()
            conn.execute(
                """
                DELETE FROM artifacts
                WHERE project_id = ? AND target_type = ? AND target_id = ?
                  AND role = ? AND attempt_index = ?
                """,
                (project_id, target_type, target_id, role, target.attempt_index),
            )
            conn.execute(
                """
                INSERT INTO artifacts (
                  id, project_id, target_type, target_id, role, attempt_index,
                  lens_id, path, title, content_sha256, size_bytes, content_type,
                  status, upload_token, created_by, created_at, updated_at, created_seq
                )
                VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, 'complete', '', ?, ?, ?, ?)
                """,
                (
                    artifact_id, project_id, target_type, target_id, role,
                    target.attempt_index, rel_path, title, sha, len(content_bytes),
                    content_type, SYSTEM_CREATED_BY, now, now,
                    next_created_seq(conn=conn, table="artifacts"),
                ),
            )
            self.store.record_event(
                conn=conn,
                project_id=project_id,
                event_type="artifact.pinned",
                target_type=target_type,
                target_id=target_id,
                payload={"artifact_id": artifact_id, "role": role, "path": rel_path},
            )
        return {"artifact_id": artifact_id, "role": role, "path": rel_path}

    def metric_sources(
        self,
        *,
        target_id: str,
        attempt_index: int,
        target_type: str = "experiment",
    ) -> list[dict[str, Any]]:
        """Metric sources for the exhibit: every complete role-'result'
        artifact for the attempt, its JSON try-parsed (non-JSON stays with
        data=None — the path label is a hint, never a gate)."""
        if self.blobs is None:
            return []
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                """
                SELECT * FROM artifacts
                WHERE status = 'complete' AND target_type = ? AND target_id = ?
                  AND role = 'result' AND attempt_index = ?
                ORDER BY path, created_seq
                """,
                (target_type, target_id, int(attempt_index)),
            ).fetchall()
        sources: list[dict[str, Any]] = []
        for row in rows:
            try:
                data = self.blobs.get(
                    namespace=str(row["project_id"]),
                    sha256=str(row["content_sha256"]),
                )
            except NotFoundError:
                continue
            try:
                parsed = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = None
            sources.append(
                {
                    "path": str(row["path"] or ""),
                    "artifact_id": str(row["id"]),
                    "sha256": str(row["content_sha256"]),
                    "submitted_at": str(row["updated_at"]),
                    "data": parsed,
                }
            )
        return sources

    # ---- internals ----

    def _require(
        self, *, conn: Connection, project_id: str, artifact_id: str
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM artifacts WHERE id = ? AND project_id = ?",
            (artifact_id, project_id),
        ).fetchone()
        if row is None:
            raise NotFoundError(
                f"artifact not found in project {project_id}: {artifact_id}"
            )
        record = row_to_dict(row=row) or {}
        record.pop("upload_token", None)  # bearer credential, never surfaced
        return record

    def _supersede_slot(self, *, conn: Connection, row: Row) -> None:
        """Resubmit replaces: delete prior complete artifacts in the same slot.

        Publish-pinned project graphs are exempt — a published reflection's
        frozen comparison base must survive later submissions to the slot."""
        pinned = self.association_targets.publish_pinned_artifact_ids(conn=conn)
        stale = conn.execute(
            """
            SELECT id FROM artifacts
            WHERE project_id = ? AND target_type = ? AND target_id = ? AND role = ?
              AND attempt_index = ? AND lens_id = ? AND path = ?
              AND status = 'complete' AND id != ?
            """,
            (
                row["project_id"], row["target_type"], row["target_id"], row["role"],
                row["attempt_index"], row["lens_id"], row["path"], row["id"],
            ),
        ).fetchall()
        for old in stale:
            if str(old["id"]) in pinned:
                continue
            conn.execute(
                "DELETE FROM artifact_figures WHERE artifact_id = ?", (old["id"],)
            )
            conn.execute("DELETE FROM artifacts WHERE id = ?", (old["id"],))

    def _mint_figure_tokens(
        self, *, conn: Connection, row: Row, data: bytes
    ) -> list[dict[str, Any]]:
        """Pending figure rows + one-time tokens for gated-markdown image links."""
        if str(row["role"]) not in MARKDOWN_FIGURE_ROLES:
            return []
        text = data.decode("utf-8", errors="replace")
        figures: list[dict[str, Any]] = []
        for link in dict.fromkeys(markdown_image_links(text)):
            token = secrets.token_urlsafe(24)
            conn.execute(
                """
                INSERT INTO artifact_figures
                  (id, artifact_id, link_path, status, upload_token, expires_at)
                VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (
                    new_id(prefix="fig"), row["id"], link, token,
                    iso_after(seconds=UPLOAD_TOKEN_TTL_SECONDS),
                ),
            )
            figures.append({"link_path": link, "token": token})
        return figures

    def _sweep_expired(self) -> None:
        """Own transaction so the sweep survives a failing access path."""
        now = now_iso()
        with self.store.transaction() as conn:
            conn.execute(
                "DELETE FROM artifact_figures WHERE status = 'pending' AND expires_at < ?",
                (now,),
            )
            conn.execute(
                "DELETE FROM artifacts WHERE status = 'pending' AND expires_at < ?",
                (now,),
            )
