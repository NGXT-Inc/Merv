# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Byte-safe transcript tails over text transports.

Management SSH and Modal exec decode text with replacement, so the remote
command emits an ASCII-safe frame:

    <total transcript size in bytes, from wc -c>\n
    <tail window, base64-encoded>

The total keeps cursors absolute after the log outgrows the tail window.
"""

from __future__ import annotations

import base64
import binascii
import shlex
from typing import Sequence

from ..sandbox_backend import TranscriptTail


TRANSCRIPT_TAIL_DEFAULT = 50_000


def transcript_tail_command(*, paths: Sequence[str], limit: int) -> str:
    """Frame the first existing path without skipping concurrent appends."""
    parts: list[str] = []
    for index, path in enumerate(paths):
        quoted = shlex.quote(path)
        parts.append(
            f"{'if' if index == 0 else 'elif'} [ -f {quoted} ]; then "
            f"wc -c < {quoted}; tail -c {int(limit)} {quoted} | base64; "
        )
    parts.append("fi")
    return "".join(parts)


def parse_transcript_tail(output: str) -> TranscriptTail:
    """Parse the frame; malformed input degrades to window-only semantics."""
    if not output:
        return TranscriptTail(data=b"", total_bytes=0)
    head, _, body = output.partition("\n")
    try:
        total = int(head.strip())
        data = base64.b64decode(body)
    except (ValueError, binascii.Error):
        data = output.encode("utf-8")
        return TranscriptTail(data=data, total_bytes=len(data))
    # The file can grow between `wc -c` and `tail`; never report a total the
    # window extends past, or window-start math would go negative.
    return TranscriptTail(data=data, total_bytes=max(total, len(data)))


__all__ = [
    "TRANSCRIPT_TAIL_DEFAULT",
    "parse_transcript_tail",
    "transcript_tail_command",
]
