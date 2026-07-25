"""merv_run launch convention: detached runs with file receipts.

The sandbox-side contract is files, not services: `merv_run <label> -- <cmd>`
detaches the command under ``$MERV_EXPERIMENT_DIR/.runs/<label>/`` and the
WRAPPER (not the command) writes ``finished_at`` then ``exit_code`` when the
command exits — so the sentinel survives SSH disconnects, and only box death
loses it. The brain observes runs by listing that directory over the same
management channel used for transcripts/metrics; no daemon, no registration
call, no provider API.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import shlex
from typing import Any


RUNS_DIR_NAME = ".runs"

# The exact charset merv_run itself enforces before it will create a run dir
# (see MERV_RUN_SCRIPT's label guard). The observer re-checks it because the
# listing is `basename` over every directory under .runs/, and NOTHING requires
# those to have been made by merv_run: anything with write access to the
# workdir — a pip package, a cloned repo's setup.py, a dataset extractor — can
# mkdir a name of its choosing, and that name then flows into the run ledger,
# tool results, logs, and the UI as if it were a run this system launched. A
# directory merv_run would have refused to create is not a run, so it is
# dropped here rather than parsed into one. Keep this in lockstep with the
# launcher's guard: a label the launcher accepts must survive this filter, or
# real runs go invisible.
SAFE_RUN_LABEL_RE = re.compile(r"[A-Za-z0-9._-]+\Z")
MERV_RUN_PATH = "/opt/merv/merv_run"

# Installed on every sandbox next to rec.sh and symlinked onto PATH.
MERV_RUN_SCRIPT = r"""#!/bin/sh
# merv_run <label> -- <command> [args...]: launch a long command detached, with
# receipts under $MERV_EXPERIMENT_DIR/.runs/<label>/ (meta.json, log.txt, and —
# written by this wrapper when the command exits — finished_at + exit_code).
# The exit_code file is the completion sentinel: it survives SSH disconnects.
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
# tr first: JSON forbids raw control bytes in a string, so ANY of them break
# the one-line receipt below — not just the newline/CR/tab that are easy to
# think of. A stray \x01 in an argument used to wipe command, pid AND
# started_at from the record. \000-\037 plus DEL covers the whole class (and
# sed is line-oriented, so it must never see a newline either).
# cut before the escaping, and before it can grow: ARG_MAX allows a command
# line into the megabytes, and the observer reads this file under a byte cap —
# a receipt truncated mid-JSON parses as nothing at all, losing command, pid
# AND started_at. Bounding the one unbounded field here keeps the whole record
# valid; a clipped command beats no metadata. Escaping at most doubles it, so
# meta.json stays comfortably inside the reader's budget.
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
    """One-shot remote listing of every run's receipts (no log bytes).

    Emits one ``===MERV_RUN <label>`` block per run dir with the meta.json body
    and the sentinel files. A missing .runs dir exits 0 with no output — the
    observer treats that as "no runs" at the cost of one cheap ssh exec.
    """
    runs_dir = f"{experiment_dir.rstrip('/')}/{RUNS_DIR_NAME}"
    # EVERY field here is attacker-controlled: a directory under .runs/ can be
    # created by anything with write access to the workdir, and it supplies its
    # own name, meta.json, exit_code and finished_at. Emitted raw, any of them
    # could carry a newline plus a `===MERV_RUN ` line and synthesize a second
    # block — forging a completion for a DIFFERENT, still-running label, which
    # _record then writes onto the genuine row. base64 output is [A-Za-z0-9+/=]
    # only, so it cannot contain the delimiter or a newline and the framing
    # becomes unforgeable. The head -c caps stop one huge file from bloating a
    # listing. base64 is already a hard bootstrap dependency (see
    # merv_run_install_lines).
    b64 = "base64 2>/dev/null | tr -d '\\n'"
    return (
        f"d={shlex.quote(runs_dir)}; [ -d \"$d\" ] || exit 0; "
        # Three globs, not one: merv_run's charset permits a leading dot, so
        # `merv_run .hidden` is a legal run that a bare */ never matches — the
        # launcher would accept it and the observer would never see it. The
        # `.[!.]*` and `..?*` forms cover dot-names without matching `.` or
        # `..`; an unmatched glob stays literal and the -d test drops it.
        "for r in \"$d\"/*/ \"$d\"/.[!.]*/ \"$d\"/..?*/; do [ -d \"$r\" ] || continue; "
        # Parameter expansion, not basename: basename appends a newline, and
        # `$(...)` then strips trailing newlines — which would silently
        # normalize a directory named "seed0\n" onto the real `seed0` row, the
        # very forgery the encoding exists to stop. ${r%/} drops the trailing
        # slash, ${b##*/} takes the last segment, and printf '%s' emits the
        # exact bytes.
        f"b=${{r%/}}; b=${{b##*/}}; "
        f"printf '===MERV_RUN %s\\n' \"$(printf '%s' \"$b\" | {b64})\"; "
        # 64 MiB is a catastrophe bound, not a size guess. A truncated
        # meta.json is INVALID JSON and loses command, pid AND started_at, so
        # this must sit where no legal receipt can reach it on ANY box —
        # including sandboxes still running the pre-fix launcher, which bounds
        # nothing. Linux derives the argv ceiling from RLIMIT_STACK (up to
        # ~6 MiB) and escaping can double it, so ~12 MiB is the true worst case;
        # this leaves 5x headroom while still refusing to stream an unbounded
        # file into memory the way an uncapped read would. Fresh boxes clip at
        # the source (esc) and land near 16 KB.
        f"printf '===META %s\\n' \"$(head -c 67108864 \"$r/meta.json\" 2>/dev/null | {b64})\"; "
        f"printf '===EXIT %s\\n' \"$(head -c 32 \"$r/exit_code\" 2>/dev/null | {b64})\"; "
        f"printf '===FIN %s\\n' \"$(head -c 64 \"$r/finished_at\" 2>/dev/null | {b64})\"; "
        "done"
    )


def parse_runs_listing(output: str) -> list[dict[str, Any]]:
    """Parse `runs_listing_command` stdout into run records.

    Each record: label, command, pid, started_at, exit_code (int | None) and
    finished_at (str, '' while running). Unparseable meta.json degrades to
    empty fields — the label and the sentinel are the load-bearing facts.
    """
    runs: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        if current is not None:
            runs.append(current)

    # Line-oriented, not split-on-delimiter: every payload arrives base64'd, so
    # no decoded byte can introduce a line or a marker. A block that opens with
    # a label merv_run would have refused is dropped along with its fields.
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
    """base64 field -> text; anything unparseable degrades to empty.

    Never raises: a garbled listing must not take down the reconcile sweep.
    """
    try:
        return base64.b64decode(field.strip(), validate=True).decode(
            "utf-8", errors="replace"
        )
    except (ValueError, binascii.Error):
        return ""


def merv_run_install_lines(*, script_b64: str) -> str:
    """Bootstrap fragment installing merv_run beside rec.sh and onto PATH.

    Also links the legacy ``rp_run`` name as a one-version compat shim for
    agents still typing the old command; remove next release.
    """
    return (
        f"printf '%s' {shlex.quote(script_b64)} | base64 -d > {MERV_RUN_PATH}\n"
        f"chmod +x {MERV_RUN_PATH}\n"
        f"ln -sf {MERV_RUN_PATH} /usr/local/bin/merv_run\n"
        f"ln -sf {MERV_RUN_PATH} /usr/local/bin/rp_run\n"
    )
