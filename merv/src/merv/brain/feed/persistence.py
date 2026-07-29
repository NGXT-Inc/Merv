# If you update this file, you must consult feed.md to see whether feed.md needs to be updated. feed.md must not exceed 100 lines.
"""Feed-owned tables and compatibility upgrades.

This module is deliberately narrow: it installs the rows used by FeedService
and contains no Feed workflow or delivery behavior.
"""

from __future__ import annotations

from ..kernel.state.store import BaseStateStore


_FEED_TABLES: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS posts (
      id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL,
      author_handle TEXT NOT NULL DEFAULT '',
      author_role TEXT NOT NULL DEFAULT 'main',
      text TEXT NOT NULL DEFAULT '',
      image_sha256 TEXT NOT NULL DEFAULT '',
      image_content_type TEXT NOT NULL DEFAULT '',
      link_url TEXT NOT NULL DEFAULT '',
      link_preview_json TEXT NOT NULL DEFAULT '{}',
      ref TEXT NOT NULL DEFAULT '',
      kind TEXT NOT NULL DEFAULT '',
      in_reply_to TEXT NOT NULL DEFAULT '',
      embed_sha256 TEXT NOT NULL DEFAULT '',
      embed_content_type TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      created_seq INTEGER NOT NULL DEFAULT 0,
      FOREIGN KEY(project_id) REFERENCES projects(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS feed_authors (
      project_id TEXT NOT NULL,
      handle TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'main',
      session_id TEXT NOT NULL DEFAULT '',
      registered_at TEXT NOT NULL,
      last_posted_at TEXT,
      PRIMARY KEY (project_id, handle),
      FOREIGN KEY(project_id) REFERENCES projects(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS post_reactions (
      project_id TEXT NOT NULL,
      post_id TEXT NOT NULL,
      kind TEXT NOT NULL,
      created_at TEXT NOT NULL,
      PRIMARY KEY (project_id, post_id, kind),
      FOREIGN KEY(project_id) REFERENCES projects(id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS feed_upload_tokens (
      token TEXT PRIMARY KEY,
      project_id TEXT NOT NULL,
      post_id TEXT NOT NULL,
      handle TEXT NOT NULL,
      text TEXT NOT NULL DEFAULT '',
      media_kind TEXT NOT NULL,
      media_path TEXT NOT NULL DEFAULT '',
      url TEXT NOT NULL DEFAULT '',
      ref TEXT NOT NULL DEFAULT '',
      kind TEXT NOT NULL DEFAULT '',
      in_reply_to TEXT NOT NULL DEFAULT '',
      expires_at TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(project_id) REFERENCES projects(id)
    )
    """,
)

_LEGACY_POST_COLUMNS: tuple[tuple[str, str], ...] = (
    ("kind", "ALTER TABLE posts ADD COLUMN kind TEXT NOT NULL DEFAULT ''"),
    (
        "in_reply_to",
        "ALTER TABLE posts ADD COLUMN in_reply_to TEXT NOT NULL DEFAULT ''",
    ),
    (
        "embed_sha256",
        "ALTER TABLE posts ADD COLUMN embed_sha256 TEXT NOT NULL DEFAULT ''",
    ),
    (
        "embed_content_type",
        "ALTER TABLE posts ADD COLUMN embed_content_type TEXT NOT NULL DEFAULT ''",
    ),
)


def install_feed_schema(store: BaseStateStore) -> None:
    """Install Feed tables and converge stores created before later columns."""
    with store.transaction() as conn:
        for statement in _FEED_TABLES:
            conn.execute(statement)
    for column, statement in _LEGACY_POST_COLUMNS:
        if _column_exists(store=store, table="posts", column=column):
            continue
        try:
            with store.transaction() as conn:
                conn.execute(statement)
        except Exception:
            # Another replica may win the probe/ALTER race. Ignore only that
            # converged outcome; all other operational failures still surface.
            if not _column_exists(store=store, table="posts", column=column):
                raise


def _column_exists(*, store: BaseStateStore, table: str, column: str) -> bool:
    try:
        with store.transaction() as conn:
            conn.execute(f"SELECT {column} FROM {table} LIMIT 0")
    except Exception:
        return False
    return True
