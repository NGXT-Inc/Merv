"""Review roles exempt from workflow-gate matching."""

from __future__ import annotations

REVIEW_GATE_EXEMPT_ROLES = frozenset({"human", "automated_check"})

def is_review_gate_exempt(*, role: str) -> bool:
    return role in REVIEW_GATE_EXEMPT_ROLES
