"""Safe local merge helper for experiment result TSV ledgers."""

from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path
from typing import Any

from ..utils import NotFoundError, ValidationError
from .repo_paths import resolve_repo_path


_INFERRED_KEY_COLUMNS = ("row_id", "result_id", "id", "trial_id", "run_id")


def merge_results_tsv(
    *,
    repo_root: Path,
    source_path: str,
    target_path: str,
    key_columns: list[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Merge one repo-local TSV into another without clobbering existing rows."""
    repo_root = Path(repo_root).resolve()
    source_rel, source_file = resolve_repo_path(
        repo_root=repo_root,
        path=source_path,
        subject="source_path",
    )
    target_rel, target_file = resolve_repo_path(
        repo_root=repo_root,
        path=target_path,
        subject="target_path",
    )
    if source_rel == target_rel:
        raise ValidationError("source_path and target_path must be different")
    if not source_file.exists():
        raise NotFoundError(f"source TSV does not exist: {source_path}")
    if not source_file.is_file():
        raise ValidationError("source_path must point to a file")
    if target_file.exists() and not target_file.is_file():
        raise ValidationError("target_path must point to a file")

    source = _read_tsv(source_file, label="source")
    target = (
        _read_tsv(target_file, label="target")
        if target_file.exists()
        else {"header": source["header"], "rows": []}
    )
    if list(source["header"]) != list(target["header"]):
        raise ValidationError(
            "source and target TSV headers must match exactly",
            details={
                "source_header": source["header"],
                "target_header": target["header"],
            },
        )

    keys = _resolve_key_columns(
        header=list(source["header"]),
        requested=key_columns or [],
    )
    source_rows = list(source["rows"])
    target_rows = list(target["rows"])
    source_by_key = _index_rows(rows=source_rows, key_columns=keys, label="source")
    target_by_key = _index_rows(rows=target_rows, key_columns=keys, label="target")

    inserted: list[dict[str, str]] = []
    skipped = 0
    conflicts: list[dict[str, Any]] = []
    for key, row in source_by_key.items():
        existing = target_by_key.get(key)
        if existing is None:
            inserted.append(row)
            continue
        if _canonical_row(existing) == _canonical_row(row):
            skipped += 1
            continue
        conflicts.append(
            {
                "key": dict(zip(keys, key, strict=True)),
                "existing": existing,
                "incoming": row,
            }
        )
    if conflicts:
        raise ValidationError(
            "incoming TSV has rows that conflict with the existing ledger",
            details={"conflicts": conflicts[:10], "conflict_count": len(conflicts)},
        )

    created = not target_file.exists()
    after_rows = [*target_rows, *inserted]
    if inserted and not dry_run:
        _write_tsv_atomic(
            path=target_file,
            header=list(source["header"]),
            rows=after_rows,
        )
    elif created and not dry_run:
        _write_tsv_atomic(path=target_file, header=list(source["header"]), rows=[])

    return {
        "ok": True,
        "source_path": source_rel,
        "target_path": target_rel,
        "key_columns": keys,
        "dry_run": dry_run,
        "created": created and not dry_run,
        "target_rows_before": len(target_rows),
        "source_rows": len(source_rows),
        "inserted_rows": len(inserted),
        "skipped_rows": skipped,
        "target_rows_after": len(after_rows),
    }


def _read_tsv(path: Path, *, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            header = reader.fieldnames
            if not header:
                raise ValidationError(f"{label} TSV must have a header row")
            if len(set(header)) != len(header):
                raise ValidationError(f"{label} TSV header contains duplicate columns")
            rows = []
            for index, row in enumerate(reader, start=2):
                if None in row:
                    raise ValidationError(
                        f"{label} TSV row {index} has more fields than the header"
                    )
                missing = [column for column, value in row.items() if value is None]
                if missing:
                    raise ValidationError(
                        f"{label} TSV row {index} has fewer fields than the header",
                        details={"missing": missing},
                    )
                rows.append(dict(row))
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{label} TSV must be UTF-8 text") from exc
    return {"header": list(header), "rows": rows}


def _resolve_key_columns(*, header: list[str], requested: list[str]) -> list[str]:
    keys = [str(column).strip() for column in requested if str(column).strip()]
    if not keys:
        keys = [column for column in _INFERRED_KEY_COLUMNS if column in header][:1]
    if not keys:
        raise ValidationError(
            "key_columns is required unless the TSV has one of: "
            + ", ".join(_INFERRED_KEY_COLUMNS)
        )
    missing = [column for column in keys if column not in header]
    if missing:
        raise ValidationError(
            "key_columns must exist in the TSV header",
            details={"missing": missing, "header": header},
        )
    if len(set(keys)) != len(keys):
        raise ValidationError("key_columns must not contain duplicates")
    return keys


def _index_rows(
    *, rows: list[dict[str, str]], key_columns: list[str], label: str
) -> dict[tuple[str, ...], dict[str, str]]:
    indexed: dict[tuple[str, ...], dict[str, str]] = {}
    duplicates: list[dict[str, str]] = []
    for row in rows:
        key = tuple(str(row.get(column) or "").strip() for column in key_columns)
        if any(not value for value in key):
            raise ValidationError(
                f"{label} TSV has a row with an empty key column",
                details={"key_columns": key_columns, "row": row},
            )
        existing = indexed.get(key)
        if existing is None:
            indexed[key] = row
            continue
        if _canonical_row(existing) != _canonical_row(row):
            duplicates.append(dict(zip(key_columns, key, strict=True)))
    if duplicates:
        raise ValidationError(
            f"{label} TSV has duplicate keys with different row values",
            details={"duplicate_keys": duplicates[:10], "duplicate_count": len(duplicates)},
        )
    return indexed


def _canonical_row(row: dict[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value or "")) for key, value in row.items()))


def _write_tsv_atomic(
    *, path: Path, header: list[str], rows: list[dict[str, str]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=header,
                delimiter="\t",
                lineterminator="\n",
                extrasaction="raise",
            )
            writer.writeheader()
            writer.writerows(rows)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
