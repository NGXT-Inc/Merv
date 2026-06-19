"""Shared project-reflection thresholds.

The project gets an advisory nudge before it gets a hard workflow block. Keep
the two numbers separate so the agent can start reflecting early without losing
the ability to queue urgent follow-up experiments until the hard cap is hit.
"""

REFLECTION_NUDGE_NEW_TERMINAL_THRESHOLD = 3
REFLECTION_BLOCK_NEW_TERMINAL_THRESHOLD = 5
