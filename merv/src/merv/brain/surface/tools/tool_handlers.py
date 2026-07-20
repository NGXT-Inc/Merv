"""Tool-name registry over composed service objects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...application.experiments.tracking_presentation import (
    tracking_connection,
    tracking_context_response,
    with_tracking_if_visible,
)
from ...mlflow import mlflow_experiment_name
from ...research_core.experiment_views import slim_experiment_state
from ...kernel.utils import ValidationError

def _attach_feed_note(
    result: dict[str, Any],
    *,
    feed: Any,
    project_id: str,
    entity_id: str,
    event: str,
) -> None:
    """Attach an optional ``feed_note`` advisory to ``result`` in place.

    Never raises: a feed hiccup (a bad connection, a schema surprise, ...)
    must not break the workflow transition, review check, or MLflow call
    whose response this rides on. Absent rather than null when there is
    nothing to say, matching how every other optional response field in this
    module (``mlflow``, ``metrics_exhibit``, ...) is attached.
    """
    try:
        note = feed.feed_note_for(
            project_id=project_id, entity_id=entity_id, event=event
        )
    except Exception:  # noqa: BLE001 - advisory only, must never block
        note = None
    if note is not None:
        result["feed_note"] = note


def _experiment_list_agent(
    *, experiments: Any, project_id: str | None = None
) -> dict[str, Any]:
    full = experiments.list_experiments(project_id=project_id)
    return {
        "experiments": [
            slim_experiment_state(experiment) for experiment in full["experiments"]
        ]
    }


def _mlflow_project_connection(
    *, mlflow_tracking: Any, project_id: str, experiments: Any
) -> dict[str, Any]:
    """Project-level MLflow connection and namespace map for direct API reads."""
    if mlflow_tracking is None or not project_id:
        return {"configured": False}
    block = dict(
        mlflow_tracking.project_context(project_id=project_id, include_credentials=True)
    )
    listed = experiments.list_experiments(project_id=project_id)["experiments"]
    block["experiments"] = [
        {
            "experiment_id": exp.get("id"),
            "name": exp.get("name") or exp.get("id"),
            "status": exp.get("status") or "",
            "intent": exp.get("intent") or "",
            "mlflow_experiment_name": mlflow_experiment_name(
                project_id=project_id, experiment_id=str(exp.get("id") or "")
            ),
        }
        for exp in listed
        if exp.get("id")
    ]
    return block


def build_control_tool_handlers(
    *,
    workflow: Any,
    projects: Any,
    project_overview: Any,
    claims: Any,
    experiments: Any,
    reflection_tools: Any,
    resources: Any,
    storage: Any | None,
    reviews: Any,
    sandboxes: Any,
    mlflow_tracking: Any,
    feed: Any,
    experiment_transition: Any,
    experiment_exhibit: Any,
) -> dict[str, Callable[..., dict[str, Any]]]:
    """Map control-plane tool names to service methods.

    This is intentionally a thin registry: composition supplies the services,
    and ToolDispatcher verifies the final name set against TOOL_CONTRACTS.
    """
    def experiment_get_state_agent(
        *, experiment_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        full = experiments.get_state(
            experiment_id=experiment_id, project_id=project_id
        )
        slim = slim_experiment_state(full)
        return with_tracking_if_visible(
            state=slim,
            tracking=mlflow_tracking,
            project_id=str(full.get("project_id") or project_id or ""),
            experiment_id=experiment_id,
            include_credentials=True,
        )

    def experiment_list_agent(
        *, project_id: str | None = None
    ) -> dict[str, Any]:
        return _experiment_list_agent(experiments=experiments, project_id=project_id)

    def experiment_transition_agent(
        *,
        experiment_id: str,
        transition: str,
        evidence: dict[str, Any] | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return experiment_transition.execute(
            experiment_id=experiment_id,
            transition=transition,
            evidence=evidence,
            project_id=project_id,
            include_tracking_credentials=True,
        )

    def mlflow_context_agent(
        *, project_id: str, experiment_id: str | None = None
    ) -> dict[str, Any]:
        if not experiment_id:
            block = _mlflow_project_connection(
                mlflow_tracking=mlflow_tracking,
                project_id=project_id,
                experiments=experiments,
            )
            return tracking_context_response(
                project_id=project_id,
                experiment_id=None,
                tracking=block,
            )
        state = experiments.get_state(experiment_id=experiment_id, project_id=project_id)
        resolved_project_id = str(state.get("project_id") or project_id or "")
        block = tracking_connection(
            tracking=mlflow_tracking,
            project_id=resolved_project_id,
            experiment_id=experiment_id,
            include_credentials=True,
            run=state.get("mlflow_run"),
        )
        return tracking_context_response(
            project_id=resolved_project_id,
            experiment_id=experiment_id,
            tracking=block,
        )

    def mlflow_finalize_run_agent(
        *,
        project_id: str,
        experiment_id: str,
        run_id: str | None = None,
        status: str | None = "FINISHED",
        wait_seconds: float = 2.0,
    ) -> dict[str, Any]:
        state = experiments.get_state(experiment_id=experiment_id, project_id=project_id)
        resolved_project_id = str(state.get("project_id") or project_id or "")
        existing_run = state.get("mlflow_run") or {}
        resolved_run_id = str(run_id or existing_run.get("run_id") or "")
        if mlflow_tracking is None:
            return {
                "project_id": resolved_project_id,
                "experiment_id": experiment_id,
                "configured": False,
                "run_id": resolved_run_id,
                "error": "MLflow tracking is not configured on this backend.",
            }
        result = mlflow_tracking.finalize_run(
            project_id=resolved_project_id,
            experiment_id=experiment_id,
            run_id=resolved_run_id,
            status=status,
            wait_seconds=wait_seconds,
        )
        run = result.get("run")
        refreshed_state = state
        persisted_run_id = str(existing_run.get("run_id") or "")
        # Only refresh the experiment's canonical run block for the run it
        # actually owns — finalizing an explicit foreign run_id must not
        # repoint the persisted identity.
        if (
            isinstance(run, dict)
            and run.get("run_id")
            and (not persisted_run_id or str(run.get("run_id")) == persisted_run_id)
        ):
            refreshed_state = experiments.record_mlflow_run(
                project_id=resolved_project_id,
                experiment_id=experiment_id,
                run=run,
                event_type="experiment.mlflow_run_refreshed",
            )
        slim = slim_experiment_state(refreshed_state)
        slim = with_tracking_if_visible(
            state=slim,
            tracking=mlflow_tracking,
            project_id=resolved_project_id,
            experiment_id=experiment_id,
            include_credentials=True,
        )
        out = dict(result)
        out["project_id"] = resolved_project_id
        out["experiment_id"] = experiment_id
        out["experiment"] = slim
        if isinstance(run, dict) and run.get("run_id"):
            _attach_feed_note(
                out,
                feed=feed,
                project_id=resolved_project_id,
                entity_id=experiment_id,
                event="mlflow_run_finalized",
            )
        return out

    def resource_find(
        *,
        resource_id: str | None = None,
        include_history: bool = False,
        kind: str | None = None,
        experiment_id: str | None = None,
        missing: bool | None = None,
        compact: bool = False,
        limit: int | None = None,
        offset: int = 0,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """resource_id → resolve one hydrated resource; otherwise list with
        filters. Both branches call the same record-service methods the former
        resource.resolve / resource.list handlers used."""
        if resource_id is not None:
            return resources.resolve(
                resource_id=resource_id,
                include_history=include_history,
                project_id=project_id,
            )
        return resources.list_resources(
            kind=kind,
            experiment_id=experiment_id,
            missing=missing,
            compact=compact,
            limit=limit,
            offset=offset,
            project_id=project_id,
        )

    def review_status_agent(
        *, target_type: str, target_id: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """Wraps reviews.status so the PRODUCER side — not the reviewer, who
        already saw the verdict at review.submit — gets the feed_note. review.status
        is hidden from the agent tools/list (agents poll workflow.status_and_next,
        whose review_gate re-reports the verdict), so this feed_note now rides the
        REST/UI review reads keyed to "the experiment under review". Only fires once
        a verdict actually exists (a bare pending-request check has nothing
        story-worthy to say yet)."""
        result = reviews.status(
            target_type=target_type, target_id=target_id, project_id=project_id
        )
        if target_type == "experiment" and result.get("reviews"):
            try:
                resolved_project_id = str(
                    experiments.get_state(
                        experiment_id=target_id, project_id=project_id
                    ).get("project_id")
                    or project_id
                    or ""
                )
            except Exception:  # noqa: BLE001 - advisory only, must never block
                resolved_project_id = ""
            if resolved_project_id:
                _attach_feed_note(
                    result,
                    feed=feed,
                    project_id=resolved_project_id,
                    entity_id=target_id,
                    event="experiment_review_verdict",
                )
        return result

    def project_control(
        *,
        action: str,
        project_id: str = "",
        name: str = "",
        summary: str = "",
        overwrite: bool = False,
        tenant_id: str | None = None,
        user_id: str = "",
    ) -> dict[str, Any]:
        # The merged `project` tool. current/connect are served by the local
        # proxy (which owns the folder→project link store) and never reach the
        # brain; create and overview forward here. If current/connect DO arrive,
        # an old proxy without the interceptor (or a direct HTTP caller) sent them.
        if action == "create":
            return projects.create(
                name=name, summary=summary, tenant_id=tenant_id, user_id=user_id
            )
        if action == "overview":
            # The whole-project read: reuse the exact claim.list and
            # experiment.list projections so overview never drifts from them.
            project = projects.get(project_id=project_id)
            return {
                "project": {
                    "id": project["id"],
                    "name": project["name"],
                    "summary": project.get("summary", ""),
                },
                "claims": claims.list_claims(project_id=project_id)["claims"],
                "experiments": _experiment_list_agent(
                    experiments=experiments, project_id=project_id
                )["experiments"],
            }
        raise ValidationError(
            f'project action="{action}" is served by the local merv '
            "proxy, not the brain. Seeing this means your Merv client "
            "is older than the brain — update the plugin (git pull) and restart "
            "your MCP client."
        )

    handlers = {
        "workflow.status_and_next": workflow.status_and_next_agent,
        "project": project_control,
        "project.update": projects.update,
        "project.get": projects.get,
        "project.list": projects.list_projects,
        "claim.create": claims.create,
        "claim.list": claims.list_claims,
        "claim.update": claims.update,
        "experiment.create": experiments.create,
        "experiment.list": experiment_list_agent,
        "experiment.get_state": experiment_get_state_agent,
        "experiment.transition": experiment_transition_agent,
        "experiment.exhibit": experiment_exhibit.preview,
        "mlflow.context": mlflow_context_agent,
        "mlflow.finalize_run": mlflow_finalize_run_agent,
        "reflection.create": reflection_tools.create,
        "reflection.get": reflection_tools.get,
        "reflection.list": reflection_tools.list,
        "reflection.transition": reflection_tools.transition,
        "resource.delete": resources.delete,
        "resource.find": resource_find,
        "review.request": reviews.request,
        "review.start": reviews.start,
        "review.submit": reviews.submit,
        "review.status": review_status_agent,
        "sandbox.options": sandboxes.options,
        "sandbox.get": sandboxes.get,
        "sandbox.list": sandboxes.list_sandboxes,
        "sandbox.release": sandboxes.release,
        "sandbox.extend": sandboxes.extend,
        "sandbox.terminal": sandboxes.terminal,
        "sandbox.runs": sandboxes.runs,
        "sandbox.health": sandboxes.health,
        "feed.register": feed.register,
        "feed.list": feed.list_posts,
    }
    if storage is not None:
        def storage_find(
            *,
            project_id: str | None = None,
            object_id: str | None = None,
            name: str | None = None,
            version: int | None = None,
            include_download: bool = True,
            kind: str | None = None,
            status: str | None = None,
            include_expired: bool = False,
            limit: int | None = None,
            offset: int = 0,
            compact: bool = False,
        ) -> dict[str, Any]:
            # object_id or name selects a single object (former storage.resolve);
            # otherwise list the ledger (former storage.list).
            if object_id or name:
                return storage.resolve(
                    project_id=project_id,
                    object_id=object_id,
                    name=name,
                    version=version,
                    include_download=include_download,
                )
            return storage.list_objects(
                project_id=project_id,
                kind=kind,
                status=status,
                include_expired=include_expired,
                limit=limit,
                offset=offset,
                compact=compact,
            )

        storage_actions: dict[str, Callable[..., dict[str, Any]]] = {
            "pin": storage.pin,
            "unpin": storage.unpin,
            "renew": storage.renew,
            "delete": storage.delete,
        }

        def storage_object(
            *, object_id: str, action: str, project_id: str | None = None
        ) -> dict[str, Any]:
            act = storage_actions.get(action)
            if act is None:
                raise ValidationError(f"unknown storage object action: {action}")
            return act(project_id=project_id, object_id=object_id)

        handlers.update(
            {
                "storage.put_object": storage.put_object,
                "storage.complete_upload": storage.complete_upload,
                "storage.find": storage_find,
                "storage.object": storage_object,
            }
        )
    return handlers


def build_local_tool_handlers(
    *,
    workflow: Any,
    projects: Any,
    project_overview: Any,
    claims: Any,
    experiments: Any,
    reflection_tools: Any,
    resources: Any,
    storage: Any | None,
    reviews: Any,
    sandboxes: Any,
    mlflow_tracking: Any,
    feed: Any,
    experiment_transition: Any,
    experiment_exhibit: Any,
    resource_register_file: Callable[..., dict[str, Any]],
    experiment_materialize_folders: Callable[..., dict[str, Any]],
    # Data-plane local IO: required — there is no control-plane fallback.
    sandbox_pull_outputs: Callable[..., dict[str, Any]],
    resource_associate: Callable[..., dict[str, Any]] | None = None,
    feed_post: Callable[..., dict[str, Any]] | None = None,
    storage_upload_file: Callable[..., dict[str, Any]] | None = None,
    storage_download_file: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Callable[..., dict[str, Any]]]:
    """Map all local-mode tool names to service methods."""
    def resource_register(
        *,
        path: str | None = None,
        paths: list[str] | None = None,
        resource_id: str | None = None,
        kind: str = "other",
        title: str = "",
        created_by: str = "codex",
        target_type: str | None = None,
        target_id: str | None = None,
        role: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Register file(s) and optionally associate, composing the same
        register_file / associate callables the two old tools used."""
        associate = (
            resource_associate if resource_associate is not None else resources.associate
        )

        def _associate(rid: str) -> dict[str, Any]:
            return associate(
                project_id=project_id,
                resource_id=rid,
                target_type=target_type,
                target_id=target_id,
                role=role,
            )

        has_target = None not in (target_type, target_id, role)
        if resource_id is not None:
            return _associate(resource_id)
        registered = resource_register_file(
            project_id=project_id,
            path=path,
            paths=paths,
            kind=kind,
            title=title,
            created_by=created_by,
        )
        if not has_target:
            return registered
        batch = registered.get("resources")
        if isinstance(batch, list):
            associations = [_associate(str(res.get("id"))) for res in batch]
            return {"resources": batch, "associations": associations, "count": len(batch)}
        return {"resource": registered, "association": _associate(str(registered.get("id")))}

    handlers = build_control_tool_handlers(
        workflow=workflow,
        projects=projects,
        project_overview=project_overview,
        claims=claims,
        experiments=experiments,
        reflection_tools=reflection_tools,
        resources=resources,
        storage=storage,
        reviews=reviews,
        sandboxes=sandboxes,
        mlflow_tracking=mlflow_tracking,
        feed=feed,
        experiment_transition=experiment_transition,
        experiment_exhibit=experiment_exhibit,
    )
    handlers.update(
        {
            "resource.register": resource_register,
            "experiment.materialize_folders": experiment_materialize_folders,
            "sandbox.request": sandboxes.request,
            "sandbox.attach": sandboxes.attach,
            "sandbox.pull_outputs": sandbox_pull_outputs,
            "feed.post": feed_post if feed_post is not None else feed.post,
        }
    )
    if storage is not None:
        handlers.update(
            {
                "storage.upload_file": (
                    storage_upload_file
                    if storage_upload_file is not None
                    else storage.upload_file
                ),
                "storage.download_file": (
                    storage_download_file
                    if storage_download_file is not None
                    else storage.download_file
                ),
            }
        )
    return handlers
