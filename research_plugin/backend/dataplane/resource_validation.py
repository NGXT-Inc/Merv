"""Local preflight lint for repo-file resources."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.artifacts import plan_sections_missing, report_problems
from ..domain.graph_lint import graph_problems
from ..domain.markdown_images import (
    MARKDOWN_FIGURE_MAX_BYTES,
    MARKDOWN_FIGURE_ROLES,
)
from ..domain.vocabulary import (
    GATED_ROLE_BYTE_CAPS,
    PROJECT_GRAPH_ROLE,
    RESOURCE_ROLES,
)
from ..utils import NotFoundError, ValidationError
from .repo_paths import resolve_repo_path
from .resource_artifacts import (
    LocalResourceArtifactReader,
    reject_absolute_markdown_image_targets,
)


def validate_local_resource_artifact(
    *, repo_root: Path, path: str, role: str
) -> dict[str, Any]:
    """Lint the current local file before register/associate mutates state."""
    repo_root = Path(repo_root).resolve()
    role = str(role or "").strip()
    problems: list[str] = []
    if role not in RESOURCE_ROLES:
        problems.append(f"unknown resource role: {role}")

    rel_path = str(path or "")
    size_bytes = 0
    try:
        rel_path, file_path = resolve_repo_path(
            repo_root=repo_root, path=path, subject="resource path"
        )
        if not file_path.exists():
            raise NotFoundError(f"resource file does not exist: {path}")
        if not file_path.is_file():
            raise ValidationError("v0.0001 resources must be files")
        data = file_path.read_bytes()
        size_bytes = len(data)
    except (OSError, NotFoundError, ValidationError) as exc:
        return _result(
            path=rel_path,
            role=role,
            size_bytes=size_bytes,
            max_bytes=GATED_ROLE_BYTE_CAPS.get(role),
            problems=[*problems, str(exc)],
        )

    max_bytes = GATED_ROLE_BYTE_CAPS.get(role)
    if max_bytes is None:
        return _result(
            path=rel_path,
            role=role,
            size_bytes=size_bytes,
            max_bytes=max_bytes,
            problems=problems,
        )
    if size_bytes > max_bytes:
        problems.append(
            f"{rel_path} is {size_bytes} bytes; the maximum for a role-{role!r} "
            f"artifact is {max_bytes} bytes"
        )

    text = data.decode("utf-8", errors="replace")
    if role in MARKDOWN_FIGURE_ROLES:
        try:
            reject_absolute_markdown_image_targets(
                markdown_rel_path=rel_path, markdown_text=text
            )
        except ValidationError as exc:
            problems.append(str(exc))

    if role == "plan":
        missing = plan_sections_missing(text)
        if missing:
            problems.append("missing required sections: " + ", ".join(missing))
    elif role == "report":
        submitted_links = _submitted_figure_links(
            repo_root=repo_root,
            rel_path=rel_path,
            text=text,
            problems=problems,
        )
        problems.extend(
            report_problems(
                text,
                figure_problem=lambda link: _figure_problem(
                    repo_root=repo_root,
                    rel_path=rel_path,
                    link=link,
                    submitted_links=submitted_links,
                ),
            )
        )
    elif role in {"graph", PROJECT_GRAPH_ROLE}:
        problems.extend(graph_problems(text))

    return _result(
        path=rel_path,
        role=role,
        size_bytes=size_bytes,
        max_bytes=max_bytes,
        problems=problems,
    )


def _submitted_figure_links(
    *, repo_root: Path, rel_path: str, text: str, problems: list[str]
) -> set[str]:
    try:
        figures = LocalResourceArtifactReader(repo_root=repo_root).submitted_figures(
            markdown_rel_path=rel_path,
            markdown_text=text,
        )
    except ValidationError as exc:
        problems.append(str(exc))
        return set()
    return {
        str(figure.get("link_path") or "")
        for figure in figures
        if figure.get("link_path")
    }


def _figure_problem(
    *,
    repo_root: Path,
    rel_path: str,
    link: str,
    submitted_links: set[str],
) -> str | None:
    if link in submitted_links:
        return None
    resolved = ((repo_root / rel_path).parent / link).resolve()
    try:
        resolved.relative_to(repo_root)
    except ValueError:
        return f"figure {link!r} escapes the repo"
    if not resolved.exists():
        return f"figure {link!r} has no submitted content: file does not exist"
    if not resolved.is_file():
        return f"figure {link!r} has no submitted content: target is not a file"
    size = resolved.stat().st_size
    if size > MARKDOWN_FIGURE_MAX_BYTES:
        return (
            f"figure {link!r} is {size} bytes; the maximum figure size is "
            f"{MARKDOWN_FIGURE_MAX_BYTES} bytes"
        )
    return f"figure {link!r} has no submitted content"


def _result(
    *,
    path: str,
    role: str,
    size_bytes: int,
    max_bytes: int | None,
    problems: list[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": not problems,
        "path": path,
        "role": role,
        "gated": max_bytes is not None,
        "size_bytes": size_bytes,
        "problems": problems,
    }
    if max_bytes is not None:
        result["max_bytes"] = max_bytes
    return result
