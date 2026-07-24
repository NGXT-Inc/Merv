"""Record-store primitives shared by brain compositions."""

from .activity import monotonic_ms
from .store import BaseStateStore, SqliteStateStore, StateStore, row_to_dict, rows_to_dicts

# The Postgres dialect (state.dialects.PostgresStateStore) is deliberately
# not re-exported here: importing it is a control-profile/test concern and
# its psycopg dependency must stay optional for local installs.

__all__ = [
    "BaseStateStore",
    "SqliteStateStore",
    "StateStore",
    "monotonic_ms",
    "row_to_dict",
    "rows_to_dicts",
]
