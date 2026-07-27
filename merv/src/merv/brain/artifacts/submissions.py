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
from merv.shared.content_summaries import content_tldr
from merv.shared.markdown_images import (
    MARKDOWN_FIGURE_MAX_BYTES,
    MARKDOWN_FIGURE_ROLES,
    figure_link_problem,
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
    MAX_SUBMITTED_TEXT_BYTES,
    SubmittedContent,
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


def _is_textual_type(content_type: str) -> bool:
    """Declared-textual: text/*, JSON/XML (incl. +json/+xml suffixes), or an
    absent declaration (the byte inspection decides)."""
    base = content_type.split(";", 1)[0].strip().lower()
    return (
        not base
        or base.startswith("text/")
        or base in ("application/json", "application/xml")
        or base.endswith(("+json", "+xml"))
    )


def _shell_quote(value: str) -> str:
    """POSIX single-quote — the agent runs the command verbatim in a shell."""
    return "'" + value.replace("'", "'\\''") + "'"


def upload_command(*, base_url: str, path: str, token: str, kind: str = "u") -> str:
    """The ready-to-run one-liner the agent executes verbatim."""
    base = (base_url or _LOCAL_API_BASE).rstrip("/")
    return f"curl -sf -T {_shell_quote(path)} '{base}/api/artifacts/{kind}/{token}'"


def _evidence(row: Row, *, tldr: str = "") -> AssociatedEvidence:
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
        tldr=tldr,
        submission_id=str(row["submission_id"] or ""),
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
        """Pin the uploaded bytes: cap, sha, blob, flip complete, supersede.

        The token is bound to (target, attempt): the target is re-resolved
        through the same resolver used at submit, and its CURRENT attempt must
        still be the one the token was minted for. A target that went terminal
        (e.g. the reflection wave published) or moved on to a new attempt
        refuses the bytes, and the pending row expires with its token —
        otherwise a pre-minted token could drift a frozen wave or land work in
        a round that already closed."""
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
            refusal = self._stale_upload_refusal(row=row)
            if refusal is not None:
                # Expire, not rollback: the delete must commit (raising here
                # would roll it back) so the dead token can never complete.
                conn.execute(
                    "DELETE FROM artifact_figures WHERE artifact_id = ?", (row["id"],)
                )
                conn.execute("DELETE FROM artifacts WHERE id = ?", (row["id"],))
            else:
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
        if refusal is not None:
            raise refusal
        return {
            "artifact_id": str(row["id"]),
            "role": role,
            "path": path,
            "sha256": sha,
            "size_bytes": len(data),
            "figures": figures,
        }

    def complete_figure_upload(self, *, token: str, data: bytes) -> dict[str, Any]:
        """Pin one figure's bytes into its document's figure set.

        A figure token inherits its document's binding: the parent artifact's
        (target, attempt) is re-resolved here exactly as the primary upload
        re-resolves its own, so a figure minted in attempt 1 cannot land in a
        document whose round has closed or whose target went terminal (audit
        ART-02). Refusal expires the document's pending figure tokens, the same
        expire-then-refuse semantics the primary upload uses.
        """
        if self.blobs is None:
            raise WorkflowError("artifact submission requires a configured blob store")
        self._sweep_expired()
        with self.store.transaction() as conn:
            row = conn.execute(
                """
                SELECT f.*, a.project_id, a.target_type, a.target_id, a.attempt_index
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
            refusal = self._stale_upload_refusal(row=row)
            if refusal is not None:
                # Expire, not rollback: the delete must commit so no figure of
                # this document can complete into the closed round either.
                conn.execute(
                    "DELETE FROM artifact_figures "
                    "WHERE artifact_id = ? AND status = 'pending'",
                    (row["artifact_id"],),
                )
            else:
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
        if refusal is not None:
            raise refusal
        return {
            "artifact_id": str(row["artifact_id"]),
            "link_path": link,
            "sha256": sha,
            "size_bytes": len(data),
        }

    def pending_upload_cap(self, *, token: str, kind: str = "u") -> int:
        """Byte cap for a pending upload token; 404s on unknown tokens so the
        transport can refuse to buffer a body for anyone without a token."""
        self._sweep_expired()
        with closing(self.store.connect()) as conn:
            if kind == "f":
                row = conn.execute(
                    "SELECT 1 FROM artifact_figures WHERE upload_token = ? AND status = 'pending'",
                    (token,),
                ).fetchone()
                if row is None:
                    raise NotFoundError(
                        "unknown, used, or expired figure token — resubmit the "
                        "document to mint fresh figure uploads"
                    )
                return MARKDOWN_FIGURE_MAX_BYTES
            row = conn.execute(
                "SELECT role FROM artifacts WHERE upload_token = ? AND status = 'pending'",
                (token,),
            ).fetchone()
        if row is None:
            raise NotFoundError(
                "unknown, used, or expired upload token — call artifact.submit again"
            )
        # Every submittable role is capped today; the figure cap bounds any
        # future uncapped role so the transport read stays memory-safe.
        return artifact_byte_cap(str(row["role"])) or MARKDOWN_FIGURE_MAX_BYTES

    # ---- reads ----

    def find(
        self,
        *,
        project_id: str | None = None,
        artifact_id: str = "",
        artifact_ids: list[str] | None = None,
        include_content: bool = False,
        target_type: str = "",
        target_id: str = "",
        role: str = "",
    ) -> dict[str, Any]:
        """Compact artifact listing, or ordered/atomic reads by one or more ids."""
        requested_ids = tuple(dict.fromkeys(artifact_ids or ()))
        with closing(self.store.connect()) as conn:
            project_id = self.store.require_project_id(conn=conn, project_id=project_id)
            if artifact_id:
                artifact = self._require(
                    conn=conn, project_id=project_id, artifact_id=artifact_id
                )
                result: dict[str, Any] = {"artifact": artifact}
                if include_content:
                    result["content"] = self._content_envelope(artifact=artifact)
                return result
            if requested_ids:
                placeholders = ", ".join("?" for _ in requested_ids)
                rows = rows_to_dicts(
                    rows=conn.execute(
                        f"""
                        SELECT * FROM artifacts
                        WHERE project_id = ? AND id IN ({placeholders})
                        """,
                        (project_id, *requested_ids),
                    ).fetchall()
                )
                by_id = {str(row["id"]): row for row in rows}
                missing = [
                    requested_id
                    for requested_id in requested_ids
                    if requested_id not in by_id
                ]
                if missing:
                    raise NotFoundError(
                        "artifacts not found in project "
                        f"{project_id}: {', '.join(missing)}",
                        details={
                            "field": "artifact_ids",
                            "artifact_ids": list(requested_ids),
                            "missing_artifact_ids": missing,
                        },
                    )
                artifacts = []
                for requested_id in requested_ids:
                    record = by_id[requested_id]
                    compact = {
                        key: record.get(key) for key in _ARTIFACT_LIST_FIELDS
                    }
                    if include_content:
                        compact["content"] = self._content_envelope(
                            artifact=record
                        )
                    artifacts.append(compact)
                return {"artifacts": artifacts, "count": len(artifacts)}
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
        """Canonical UI wire shape: {content, is_binary, size_bytes,
        content_type, available}. Binary = declared non-text content type
        (anything but text/*, JSON, XML, or empty), a NUL byte, or a
        strict-UTF-8 decode failure — never the filename."""
        artifact = self.resolve(artifact_id=artifact_id, project_id=project_id)
        return self._content_envelope(artifact=artifact)

    def _content_envelope(self, *, artifact: dict[str, Any]) -> dict[str, Any]:
        """Read submitted bytes for an already tenant-checked artifact row."""
        content_type = str(artifact.get("content_type") or "")
        data = None
        if self.blobs is not None and artifact.get("status") == "complete":
            try:
                data = self.blobs.get(
                    namespace=str(artifact["project_id"]),
                    sha256=str(artifact.get("content_sha256") or ""),
                )
            except NotFoundError:
                data = None
        text, is_binary = None, False
        if data is not None:
            if not _is_textual_type(content_type) or b"\x00" in data:
                is_binary = True
            else:
                try:
                    text = data.decode("utf-8")
                except UnicodeDecodeError:
                    is_binary = True
        return {
            "content": text,
            "is_binary": is_binary,
            "size_bytes": int(artifact.get("size_bytes") or 0),
            "content_type": content_type,
            "available": data is not None,
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
            grouped[str(row["target_id"])].append(
                _evidence(row, tldr=self._content_tldr(row=row))
            )
        return {tid: tuple(items) for tid, items in grouped.items()}

    def _content_tldr(self, *, row: Row) -> str:
        """Summarize immutable submitted bytes without exposing the full body."""

        content = None
        if self.blobs is not None and row["content_sha256"]:
            try:
                data = self.blobs.get(
                    namespace=str(row["project_id"]),
                    sha256=str(row["content_sha256"]),
                )
                content = data.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - macro context is best-effort
                pass
        return content_tldr(
            content,
            role=str(row["role"] or ""),
            path=str(row["path"] or ""),
        )

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

    def bounded_text_for_artifact(self, *, artifact_id: str) -> SubmittedContent:
        """Best-effort submitted text capped to the gated-content endpoint limit."""
        artifact_id = str(artifact_id)
        if not artifact_id or self.blobs is None:
            return SubmittedContent(
                artifact_id=artifact_id,
                content=None,
                size_bytes=0,
                truncated=False,
            )
        with closing(self.store.connect()) as conn:
            row = conn.execute(
                """
                SELECT project_id, content_sha256, size_bytes
                FROM artifacts
                WHERE id = ? AND status = 'complete'
                """,
                (artifact_id,),
            ).fetchone()
        if row is None:
            return SubmittedContent(
                artifact_id=artifact_id,
                content=None,
                size_bytes=0,
                truncated=False,
            )
        size_bytes = int(row["size_bytes"] or 0)
        try:
            data = self.blobs.get(
                namespace=str(row["project_id"]),
                sha256=str(row["content_sha256"]),
            )
        except NotFoundError:
            return SubmittedContent(
                artifact_id=artifact_id,
                content=None,
                size_bytes=size_bytes,
                truncated=False,
            )
        bounded = data[:MAX_SUBMITTED_TEXT_BYTES]
        text = bounded.decode("utf-8", errors="replace")
        # Replacement characters can encode to more bytes than the invalid
        # source byte they replace. Re-trim the decoded form so the response
        # bound is exact even for malformed legacy content.
        encoded = text.encode("utf-8")
        if len(encoded) > MAX_SUBMITTED_TEXT_BYTES:
            text = encoded[:MAX_SUBMITTED_TEXT_BYTES].decode(
                "utf-8", errors="ignore"
            )
        return SubmittedContent(
            artifact_id=artifact_id,
            content=text,
            size_bytes=size_bytes,
            truncated=len(data) > MAX_SUBMITTED_TEXT_BYTES,
        )

    def submitted_evidence(
        self, *, artifact_ids: tuple[str, ...]
    ) -> tuple[SubmittedEvidence, ...]:
        """Best-effort submitted text for exactly these artifacts, oldest first.

        Reads by id so a caller's pinned set arrives intact — nothing is
        re-derived from role or path, which cannot tell per-lens siblings
        apart."""
        ids = list(dict.fromkeys(str(one) for one in artifact_ids if one))
        if self.blobs is None or not ids:
            return ()
        placeholders = ", ".join("?" for _id in ids)
        with closing(self.store.connect()) as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM artifacts
                WHERE status = 'complete' AND id IN ({placeholders})
                ORDER BY created_seq
                """,
                (*ids,),
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
                    lens_id=str(row["lens_id"] or ""),
                    path=str(row["path"] or ""),
                    artifact_id=str(row["id"]),
                    submission_id=str(row["submission_id"] or ""),
                    order=int(row["created_seq"] or 0),
                    content=content,
                    submitted_at=str(row["updated_at"] or row["created_at"] or ""),
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
            # Same seal immunity as _supersede_slot: replace the exhibit being
            # assembled, never one a round already froze. Re-pinning after a
            # send_back_to_running would otherwise delete the previous round's
            # exhibit — the metrics record of the round a reviewer rejected.
            # Every reader takes the newest per slot, and the exhibit path is
            # fixed per experiment, so the survivors are history, not rivals.
            conn.execute(
                """
                DELETE FROM artifacts
                WHERE project_id = ? AND target_type = ? AND target_id = ?
                  AND role = ? AND attempt_index = ? AND submission_id = ''
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
        """Metric sources for the exhibit: the newest complete role-'result'
        artifact per path for the attempt, its JSON try-parsed (non-JSON stays
        with data=None — the path label is a hint, never a gate).

        Newest-per-path, not every row: send_back_to_running keeps the same
        attempt_index, so once a rejected round's results.json survives as
        sealed history both rows match here. Republishing the stale one would
        put two contradictory values for one filename inside the
        system-authored exhibit, which the report is then gated on."""
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
        # ORDER BY path, created_seq → last write per (lens, path) wins.
        newest: dict[tuple[str, str], Row] = {}
        for row in rows:
            newest[(str(row["lens_id"]), str(row["path"]))] = row
        rows = list(newest.values())
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

    def seal(
        self,
        *,
        conn: Connection,
        project_id: str,
        target_type: str,
        target_id: str,
        attempt_index: int,
        transition: str,
    ) -> str:
        """Freeze the target's live composition as one submission attempt.

        Runs on the CALLER's already-open write connection — Research owns the
        transition transaction, and opening our own here would take a second
        connection and deadlock against it (BEGIN IMMEDIATE on SQLite, the DSN
        advisory lock on PostgreSQL). Same shape as the existing reverse call
        into publish_pinned_artifact_ids.

        `status = 'complete'` is required: a pending row would otherwise be
        sealed before its bytes land, and could then never be superseded.
        """
        submission_id = new_id(prefix="sub")
        conn.execute(
            """
            INSERT INTO submissions
              (id, project_id, target_type, target_id, attempt_index,
               transition, created_at, created_seq)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                submission_id, project_id, target_type, target_id,
                int(attempt_index), transition, now_iso(),
                next_created_seq(conn=conn, table="submissions"),
            ),
        )
        conn.execute(
            """
            UPDATE artifacts SET submission_id = ?
            WHERE project_id = ? AND target_type = ? AND target_id = ?
              AND attempt_index = ? AND status = 'complete' AND submission_id = ''
            """,
            (
                submission_id, project_id, target_type, target_id,
                int(attempt_index),
            ),
        )
        return submission_id

    def submissions_for_targets(
        self, *, conn: Connection, target_type: str, target_ids: tuple[str, ...]
    ) -> dict[str, list[dict[str, Any]]]:
        """Sealed rounds per target, oldest first. One query for the whole
        batch so the project dashboard stays constant-cost."""
        if not target_ids:
            return {}
        placeholders = ", ".join("?" for _ in target_ids)
        rows = rows_to_dicts(
            rows=conn.execute(
                f"""
                SELECT id, target_id, attempt_index, transition, created_at,
                       created_seq
                FROM submissions
                WHERE target_type = ? AND target_id IN ({placeholders})
                ORDER BY created_seq
                """,
                (target_type, *target_ids),
            ).fetchall()
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row.pop("target_id")), []).append(row)
        return grouped

    def latest_submission_id(
        self, *, conn: Connection, target_type: str, target_id: str, attempt_index: int
    ) -> str:
        """Newest sealed submission for the attempt, '' when none exists."""
        row = conn.execute(
            """
            SELECT id FROM submissions
            WHERE target_type = ? AND target_id = ? AND attempt_index = ?
            ORDER BY created_seq DESC
            """,
            (target_type, target_id, int(attempt_index)),
        ).fetchone()
        return str(row["id"]) if row is not None else ""

    def _stale_upload_refusal(self, *, row: Row) -> ValidationError | None:
        """Re-run submit-time target resolution for a pending upload.

        Returns the refusal to raise once the expiry commits — None while the
        SAME attempt on the same target still accepts submissions. Attempt
        identity is part of that check: a token minted in attempt 1 promised
        bytes to a round that a later attempt has closed (audit ART-02)."""
        try:
            target = self.association_targets.resolve(
                target_type=str(row["target_type"]), target_id=str(row["target_id"])
            )
        except (NotFoundError, ValidationError) as exc:
            reason = getattr(exc, "message", None) or str(exc)
            return ValidationError(
                f"upload refused — {reason}. This upload token has expired; "
                "submit new work against a live target with artifact.submit"
            )
        minted_for = int(row["attempt_index"])
        if int(target.attempt_index) != minted_for:
            return ValidationError(
                f"upload refused — attempt superseded. This token was minted for "
                f"attempt {minted_for} and attempt {target.attempt_index} is now "
                "open; call artifact.submit again to upload into the current one"
            )
        return None

    def _supersede_slot(self, *, conn: Connection, row: Row) -> None:
        """Resubmit replaces: delete prior complete artifacts in the same slot.

        Only UNSEALED rows (submission_id '') can be deleted. Once a forward
        transition seals a round's composition, those rows are immutable
        history: that is what keeps the report of a rejected submission
        retrievable instead of leaving an unreachable blob behind. Within the
        round being assembled the behaviour is unchanged — resubmit report.md
        three times before requesting review and you still get one row.

        Publish-pinned project graphs are exempt — a published reflection's
        frozen comparison base must survive later submissions to the slot."""
        pinned = self.association_targets.publish_pinned_artifact_ids(conn=conn)
        stale = conn.execute(
            """
            SELECT id FROM artifacts
            WHERE project_id = ? AND target_type = ? AND target_id = ? AND role = ?
              AND attempt_index = ? AND lens_id = ? AND path = ?
              AND status = 'complete' AND submission_id = '' AND id != ?
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
            problem = figure_link_problem(link)
            if problem:
                # Raising rolls the transaction back: the artifact stays
                # pending and the same token accepts the fixed document.
                raise ValidationError(f"{problem} — fix the link and re-upload")
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
