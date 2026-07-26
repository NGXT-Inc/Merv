"""SQL adapter for Surface-owned OAuth state."""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import Any

from ..kernel.env import env_int
from ..kernel.state.store import BaseStateStore, row_to_dict
from ..kernel.utils import format_iso
from .oauth import (
    DEFAULT_MAX_CLIENTS,
    DEFAULT_UNUSED_CLIENT_TTL_DAYS,
    MAX_CLIENTS_ENV_VAR,
    OPPORTUNISTIC_PRUNE_LIMIT,
    UNUSED_CLIENT_TTL_DAYS_ENV_VAR,
    AuthorizationCode,
    OAuthClient,
    OAuthError,
    RefreshToken,
)
from .project_keys import PROJECT_GRANT


def _json_list(values: tuple[str, ...] | list[str]) -> str:
    return json.dumps(list(values), separators=(",", ":"))


# A registration nobody ever authorized. Shared by the scheduled sweep and the
# bounded prune the registration path runs itself, so the two can never drift
# into deleting different rows.
_UNUSED_CLIENT_PREDICATE = """
  created_at < ?
  AND client_id NOT IN (SELECT client_id FROM oauth_authorization_codes)
  AND client_id NOT IN (SELECT client_id FROM oauth_refresh_tokens)
"""


class SqlOAuthRepository:
    def __init__(
        self,
        *,
        store: BaseStateStore,
        unused_client_ttl_days: int | None = None,
        max_clients: int | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._store = store
        configured = (
            int(unused_client_ttl_days)
            if unused_client_ttl_days is not None
            else env_int(
                UNUSED_CLIENT_TTL_DAYS_ENV_VAR,
                DEFAULT_UNUSED_CLIENT_TTL_DAYS,
                env=env,
                strict=False,
            )
        )
        # A zero/negative horizon would delete a client mid-authorization.
        self.unused_client_ttl_days = max(1, configured)
        cap = (
            int(max_clients)
            if max_clients is not None
            else env_int(MAX_CLIENTS_ENV_VAR, DEFAULT_MAX_CLIENTS, env=env, strict=False)
        )
        # A zero/negative cap would refuse the very first registration.
        self.max_clients = max(1, cap)

    def get_or_create_client(self, *, client: OAuthClient) -> OAuthClient:
        """Resolve identical metadata to one row, or insert it — in one commit.

        The lookup and the insert share a single store transaction, so the
        store's global writer serialization (SQLite ``BEGIN IMMEDIATE``, the
        Postgres advisory lock) makes them atomic: two identical registrations
        racing each other cannot both miss the lookup and both insert a row
        (audit AUTH-03). The same commit also bounds the table — see
        ``_prune_unused`` and ``max_clients``.
        """
        with self._store.transaction() as conn:
            existing = _client(
                conn.execute(
                    """
                    SELECT * FROM oauth_clients
                    WHERE client_name = ? AND redirect_uris_json = ?
                      AND grant_types_json = ?
                    ORDER BY created_at, client_id
                    """,
                    (
                        client.client_name,
                        _json_list(client.redirect_uris),
                        _json_list(client.grant_types),
                    ),
                ).fetchone()
            )
            if existing is not None:
                return existing
            # Cleanup that does not depend on anyone scheduling it: every
            # registration pays for a bounded slice of the sweep.
            self._prune_unused(
                conn=conn, cutoff=self._cutoff(None), limit=OPPORTUNISTIC_PRUNE_LIMIT
            )
            stored = row_to_dict(
                row=conn.execute("SELECT COUNT(*) AS total FROM oauth_clients").fetchone()
            )
            if int((stored or {}).get("total") or 0) >= self.max_clients:
                raise OAuthError(
                    "temporarily_unavailable",
                    "this server is holding the maximum number of registered "
                    f"clients ({self.max_clients}); retry once unused "
                    "registrations age out, or raise "
                    f"{MAX_CLIENTS_ENV_VAR}",
                )
            conn.execute(
                """
                INSERT INTO oauth_clients (
                  client_id, client_name, redirect_uris_json, grant_types_json,
                  created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    client.client_id,
                    client.client_name,
                    _json_list(client.redirect_uris),
                    _json_list(client.grant_types),
                    client.created_at,
                ),
            )
        return client

    def client_by_id(self, *, client_id: str) -> OAuthClient | None:
        with closing(self._store.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)
            ).fetchone()
        return _client(row)

    def prune(self, *, now: datetime | None = None) -> dict[str, Any]:
        """Delete registrations past the horizon that never authorized anything.

        Reports its own outcome: a failed sweep says ``ok`` False and names the
        error rather than returning zero, which would read as a healthy pass
        that found nothing (audit OPS-03).
        """
        cutoff = self._cutoff(now)
        try:
            with self._store.transaction() as conn:
                deleted = self._prune_unused(conn=conn, cutoff=cutoff, limit=None)
        except Exception as exc:  # noqa: BLE001 -- one sweep must not abort the pass
            return {"deleted": 0, "ok": False, "cutoff": cutoff, "error": str(exc)[:200]}
        return {"deleted": deleted, "ok": True, "cutoff": cutoff}

    def _cutoff(self, now: datetime | None) -> str:
        return format_iso(
            (now or datetime.now(tz=UTC))
            - timedelta(days=self.unused_client_ttl_days)
        )

    def _prune_unused(self, *, conn: Any, cutoff: str, limit: int | None) -> int:
        """Delete unused registrations older than ``cutoff``, at most ``limit``.

        ``limit`` None is the full scheduled sweep; a number keeps the work a
        registration does on its own behalf bounded and predictable. The
        subquery form (rather than ``DELETE ... LIMIT``) is the one both
        dialects accept.
        """
        if limit is None:
            cursor = conn.execute(
                f"DELETE FROM oauth_clients WHERE {_UNUSED_CLIENT_PREDICATE}", (cutoff,)
            )
        else:
            cursor = conn.execute(
                f"""
                DELETE FROM oauth_clients WHERE client_id IN (
                  SELECT client_id FROM oauth_clients
                  WHERE {_UNUSED_CLIENT_PREDICATE}
                  ORDER BY created_at LIMIT ?
                )
                """,
                (cutoff, limit),
            )
        return max(0, int(getattr(cursor, "rowcount", 0) or 0))

    def insert_code(self, *, code: AuthorizationCode) -> None:
        with self._store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO oauth_authorization_codes (
                  code_digest, client_id, redirect_uri, owner_user_id, project_id,
                  grant_scope, code_challenge, resource, created_at, expires_at,
                  consumed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    code.code_digest,
                    code.client_id,
                    code.redirect_uri,
                    code.owner_user_id,
                    code.project_id,
                    code.grant_scope,
                    code.code_challenge,
                    code.resource,
                    code.created_at,
                    code.expires_at,
                    code.consumed_at,
                ),
            )

    def code_by_digest(self, *, digest: str) -> AuthorizationCode | None:
        with closing(self._store.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM oauth_authorization_codes WHERE code_digest = ?",
                (digest,),
            ).fetchone()
        data = row_to_dict(row=row)
        if data is None:
            return None
        return AuthorizationCode(
            code_digest=str(data["code_digest"]),
            client_id=str(data["client_id"]),
            redirect_uri=str(data["redirect_uri"]),
            owner_user_id=str(data["owner_user_id"]),
            project_id=str(data["project_id"]),
            grant_scope=str(data.get("grant_scope") or PROJECT_GRANT),
            code_challenge=str(data["code_challenge"]),
            resource=str(data["resource"]),
            created_at=str(data["created_at"]),
            expires_at=str(data["expires_at"]),
            consumed_at=(str(data["consumed_at"]) if data.get("consumed_at") else None),
        )

    def consume_code(self, *, digest: str, consumed_at: str) -> bool:
        with self._store.transaction() as conn:
            row = conn.execute(
                """
                SELECT code_digest FROM oauth_authorization_codes
                WHERE code_digest = ? AND consumed_at IS NULL AND expires_at > ?
                """,
                (digest, consumed_at),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                """
                UPDATE oauth_authorization_codes SET consumed_at = ?
                WHERE code_digest = ? AND consumed_at IS NULL
                """,
                (consumed_at, digest),
            )
        return True

    def insert_refresh_token(self, *, token: RefreshToken) -> None:
        with self._store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO oauth_refresh_tokens (
                  id, family_id, secret_digest, client_id, owner_user_id, project_id,
                  grant_scope, resource, current_key_id, parent_token_id,
                  created_at, expires_at, consumed_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token.id,
                    token.family_id,
                    token.secret_digest,
                    token.client_id,
                    token.owner_user_id,
                    token.project_id,
                    token.grant_scope,
                    token.resource,
                    token.current_key_id,
                    token.parent_token_id,
                    token.created_at,
                    token.expires_at,
                    token.consumed_at,
                    token.revoked_at,
                ),
            )

    def refresh_token_by_digest(self, *, digest: str) -> RefreshToken | None:
        with closing(self._store.connect()) as conn:
            row = conn.execute(
                "SELECT * FROM oauth_refresh_tokens WHERE secret_digest = ?",
                (digest,),
            ).fetchone()
        return _refresh_token(row)

    def consume_refresh_token(self, *, token_id: str, consumed_at: str) -> bool:
        with self._store.transaction() as conn:
            row = conn.execute(
                """
                SELECT r.id FROM oauth_refresh_tokens r
                JOIN project_api_keys k ON k.id = r.current_key_id
                WHERE r.id = ? AND r.consumed_at IS NULL AND r.revoked_at IS NULL
                  AND r.expires_at > ? AND k.revoked_at IS NULL
                """,
                (token_id, consumed_at),
            ).fetchone()
            if row is None:
                return False
            conn.execute(
                """
                UPDATE oauth_refresh_tokens SET consumed_at = ?
                WHERE id = ? AND consumed_at IS NULL
                """,
                (consumed_at, token_id),
            )
        return True

    def revoke_refresh_family_and_key_lineage(
        self,
        *,
        family_id: str,
        key_id: str,
        project_id: str,
        owner_user_id: str,
        revoked_at: str,
    ) -> None:
        """Revoke replay authority and every derived bearer in one commit."""
        with self._store.transaction() as conn:
            conn.execute(
                """
                UPDATE oauth_refresh_tokens
                SET revoked_at = COALESCE(revoked_at, ?)
                WHERE family_id = ?
                """,
                (revoked_at, family_id),
            )
            conn.execute(
                """
                WITH RECURSIVE lineage(id) AS (
                  SELECT id FROM project_api_keys WHERE id = ?
                  UNION ALL
                  SELECT child.id FROM project_api_keys child
                  JOIN lineage parent ON child.parent_key_id = parent.id
                )
                UPDATE project_api_keys SET revoked_at = COALESCE(revoked_at, ?)
                WHERE id IN (SELECT id FROM lineage)
                  AND project_id = ? AND owner_user_id = ?
                """,
                (key_id, revoked_at, project_id, owner_user_id),
            )


def _client(row: Any) -> OAuthClient | None:
    data = row_to_dict(row=row)
    if data is None:
        return None
    return OAuthClient(
        client_id=str(data["client_id"]),
        client_name=str(data["client_name"]),
        redirect_uris=tuple(json.loads(str(data["redirect_uris_json"]))),
        grant_types=tuple(json.loads(str(data["grant_types_json"]))),
        created_at=str(data["created_at"]),
    )


def _refresh_token(row: Any) -> RefreshToken | None:
    data = row_to_dict(row=row)
    if data is None:
        return None
    return RefreshToken(
        id=str(data["id"]),
        family_id=str(data["family_id"]),
        secret_digest=str(data["secret_digest"]),
        client_id=str(data["client_id"]),
        owner_user_id=str(data["owner_user_id"]),
        project_id=str(data["project_id"]),
        grant_scope=str(data.get("grant_scope") or PROJECT_GRANT),
        resource=str(data["resource"]),
        current_key_id=str(data["current_key_id"]),
        parent_token_id=(
            str(data["parent_token_id"]) if data.get("parent_token_id") else None
        ),
        created_at=str(data["created_at"]),
        expires_at=str(data["expires_at"]),
        consumed_at=(str(data["consumed_at"]) if data.get("consumed_at") else None),
        revoked_at=(str(data["revoked_at"]) if data.get("revoked_at") else None),
    )


__all__ = ["SqlOAuthRepository"]
