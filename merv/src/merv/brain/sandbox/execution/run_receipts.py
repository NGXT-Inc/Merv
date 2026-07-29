# If you update this file, you must consult sandbox.md to see whether sandbox.md needs to be updated. sandbox.md must not exceed 100 lines.
"""Detached ``merv_run`` commands with filesystem receipts.

The wrapper writes ``finished_at`` before the ``exit_code`` sentinel, which
survives SSH disconnects and requires no sandbox daemon.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import shlex
from typing import Any


RUNS_DIR_NAME = ".runs"

# Keep this charset identical to the launcher. Untrusted workdir contents can
# create arbitrary directories, but only launcher-valid names become receipts.
SAFE_RUN_LABEL_RE = re.compile(r"[A-Za-z0-9._-]+\Z")
MERV_RUN_PATH = "/opt/merv/merv_run"

MERV_RUN_SCRIPT = r"""#!/bin/sh
# Detached command with receipts under .runs/<label>; exit_code is the sentinel.
set -u
usage() { echo 'usage: merv_run <label> -- <command> [args...]' >&2; exit 2; }
[ $# -ge 3 ] || usage
label=$1; shift
[ "$1" = '--' ] || usage
shift
case $label in
  *[!A-Za-z0-9._-]*|'') echo "merv_run: label must be non-empty [A-Za-z0-9._-]" >&2; exit 2 ;;
esac
runs=${MERV_EXPERIMENT_DIR:?merv_run: MERV_EXPERIMENT_DIR is not set}/.runs
dir=$runs/$label
# mkdir without -p is the duplicate-label guard: refuse rather than suffix so
# labels stay stable for the observer.
if ! mkdir -p "$runs" || ! mkdir "$dir" 2>/dev/null; then
  echo "merv_run: run '$label' already exists in $runs — pick a new label" >&2
  exit 2
fi
# Strip every JSON control byte before line-oriented escaping. Bound first so
# escaping cannot push a legal receipt beyond the observer's read cap.
esc() { printf '%s' "$1" | tr '\000-\037\177' ' ' | cut -c1-8000 | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }
# The watcher (not the command) writes the receipts: finished_at first, then
# exit_code — the sentinel is last so its presence implies a complete record.
WATCH='dir=$1; shift
"$@" >"$dir/log.txt" 2>&1
rc=$?
date -u +%Y-%m-%dT%H:%M:%SZ >"$dir/finished_at"
echo "$rc" >"$dir/exit_code"'
# setsid detaches from the SSH session so a disconnect cannot signal the run;
# hosts without setsid (macOS test runs) still detach via nohup + background.
if command -v setsid >/dev/null 2>&1; then
  setsid nohup sh -c "$WATCH" merv_run_watch "$dir" "$@" </dev/null >/dev/null 2>&1 &
else
  nohup sh -c "$WATCH" merv_run_watch "$dir" "$@" </dev/null >/dev/null 2>&1 &
fi
pid=$!
printf '{"label":"%s","command":"%s","pid":%d,"started_at":"%s"}\n' \
  "$(esc "$label")" "$(esc "$*")" "$pid" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$dir/meta.json"
echo "merv_run: started '$label' (pid $pid) — log: $dir/log.txt (sentinel: $dir/exit_code)"
"""


def runs_listing_command(*, experiment_dir: str) -> str:
    """List receipt metadata and sentinels, never log bytes."""
    runs_dir = f"{experiment_dir.rstrip('/')}/{RUNS_DIR_NAME}"
    # All workdir fields are untrusted. Base64 prevents delimiter injection;
    # byte caps prevent unbounded remote output.
    b64 = "base64 2>/dev/null | tr -d '\\n'"
    return (
        f"d={shlex.quote(runs_dir)}; [ -d \"$d\" ] || exit 0; "
        # Include legal dot-prefixed labels without matching "." or "..".
        "for r in \"$d\"/*/ \"$d\"/.[!.]*/ \"$d\"/..?*/; do [ -d \"$r\" ] || continue; "
        # Parameter expansion preserves bytes that basename/substitution normalize.
        f"b=${{r%/}}; b=${{b##*/}}; "
        f"printf '===MERV_RUN %s\\n' \"$(printf '%s' \"$b\" | {b64})\"; "
        # Catastrophe cap above any legal argv-derived receipt, including old boxes.
        f"printf '===META %s\\n' \"$(head -c 67108864 \"$r/meta.json\" 2>/dev/null | {b64})\"; "
        f"printf '===EXIT %s\\n' \"$(head -c 32 \"$r/exit_code\" 2>/dev/null | {b64})\"; "
        f"printf '===FIN %s\\n' \"$(head -c 64 \"$r/finished_at\" 2>/dev/null | {b64})\"; "
        "done"
    )


def parse_runs_listing(output: str) -> list[dict[str, Any]]:
    """Parse framed receipts; malformed metadata keeps label and sentinel facts."""
    runs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        if current is not None:
            runs.append(current)

    # Base64 framing makes line-oriented parsing injection-safe.
    for line in output.splitlines():
        if line.startswith("===MERV_RUN "):
            flush()
            label = _decode(line[len("===MERV_RUN "):])
            current = (
                {
                    "label": label,
                    "command": "",
                    "pid": None,
                    "started_at": "",
                    "exit_code": None,
                    "finished_at": "",
                }
                if SAFE_RUN_LABEL_RE.fullmatch(label)
                else None
            )
        elif current is None:
            continue
        elif line.startswith("===META "):
            try:
                parsed = json.loads(_decode(line[len("===META "):]) or "{}")
            except ValueError:
                parsed = {}
            if isinstance(parsed, dict):
                current["command"] = str(parsed.get("command") or "")
                current["pid"] = parsed.get("pid")
                current["started_at"] = str(parsed.get("started_at") or "")
        elif line.startswith("===EXIT "):
            try:
                current["exit_code"] = int(_decode(line[len("===EXIT "):]).strip())
            except ValueError:
                current["exit_code"] = None
        elif line.startswith("===FIN "):
            current["finished_at"] = _decode(line[len("===FIN "):]).strip()
    flush()
    return runs


def _decode(field: str) -> str:
    """Decode without letting a garbled listing abort reconciliation."""
    try:
        return base64.b64decode(field.strip(), validate=True).decode(
            "utf-8", errors="replace"
        )
    except (ValueError, binascii.Error):
        return ""


def merv_run_install_lines(*, script_b64: str) -> str:
    """Install ``merv_run`` plus the one-release ``rp_run`` compatibility link."""
    return (
        f"printf '%s' {shlex.quote(script_b64)} | base64 -d > {MERV_RUN_PATH}\n"
        f"chmod +x {MERV_RUN_PATH}\n"
        f"ln -sf {MERV_RUN_PATH} /usr/local/bin/merv_run\n"
        f"ln -sf {MERV_RUN_PATH} /usr/local/bin/rp_run\n"
    )
