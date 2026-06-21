"""Shared project-reflection thresholds."""

from collections.abc import Iterable, Mapping

# The project gets an advisory nudge before it gets a hard workflow block. Keep
# the two numbers separate so urgent follow-up experiments remain possible until
# the hard cap is hit.
REFLECTION_NUDGE_NEW_TERMINAL_THRESHOLD = 3
REFLECTION_BLOCK_NEW_TERMINAL_THRESHOLD = 5


def covered_terminal_ids(corpus: Mapping[str, object] | None) -> set[str]:
    """Ids of terminal experiments a published reflection corpus already covers.

    Single source of truth for reflection-drift: callers diff this against the
    project's current terminal experiments. Tolerates a missing/empty corpus
    and non-dict list entries so a malformed snapshot never raises here."""
    if not corpus:
        return set()
    entries = corpus.get("terminal_experiments") or []
    return {
        str(exp.get("id"))
        for exp in entries
        if isinstance(exp, Mapping)
    }


def terminal_drift_count(
    *, current_terminal_ids: Iterable[str], corpus: Mapping[str, object] | None
) -> int:
    """Count of current terminal experiments not yet covered by the corpus."""
    return len(set(current_terminal_ids) - covered_terminal_ids(corpus))
