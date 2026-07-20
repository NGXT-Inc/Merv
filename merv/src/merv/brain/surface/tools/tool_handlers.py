"""Tool-name registry over composed service objects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...research_core.experiment_views import slim_experiment_state
from ...kernel.utils import ValidationError


def _experiment_list_agent(
    *, experiments: Any, project_id: str | None = None
) -> dict[str, Any]:
    full = experiments.list_experiments(project_id=project_id)
    return {
        "experiments": [
            slim_experiment_state(experiment) for experiment in full["experiments"]
        ]
    }


def build_control_tool_handlers(
    *,
    workflow: Any,
    projects: Any,
    claims: Any,
    experiments: Any,
    reflection_tools: Any,
    resources: Any,
    storage: Any | None,
    reviews: Any,
    sandboxes: Any,
    feed: Any,
    experiment_transition: Any,
    experiment_exhibit: Any,
    tracking_context: Any,
    tracking_finalize: Any,
) -> dict[str, Callable[..., dict[str, Any]]]:
    """Map control-plane tool names to service methods.

    This is intentionally a thin registry: composition supplies the services,
    and ToolDispatcher verifies the final name set against TOOL_CONTRACTS.
    """
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
                try:
                    note = feed.feed_note_for(
                        project_id=resolved_project_id,
                        entity_id=target_id,
                        event="experiment_review_verdict",
                    )
                except Exception:  # advisory only, must never block
                    note = None
                if note is not None:
                    result["feed_note"] = note
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
        "experiment.get_state": tracking_context.experiment,
        "experiment.transition": experiment_transition_agent,
        "experiment.exhibit": experiment_exhibit.preview,
        "mlflow.context": tracking_context.execute,
        "mlflow.finalize_run": tracking_finalize.execute,
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
    claims: Any,
    experiments: Any,
    reflection_tools: Any,
    resources: Any,
    storage: Any | None,
    reviews: Any,
    sandboxes: Any,
    feed: Any,
    experiment_transition: Any,
    experiment_exhibit: Any,
    tracking_context: Any,
    tracking_finalize: Any,
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
        claims=claims,
        experiments=experiments,
        reflection_tools=reflection_tools,
        resources=resources,
        storage=storage,
        reviews=reviews,
        sandboxes=sandboxes,
        feed=feed,
        experiment_transition=experiment_transition,
        experiment_exhibit=experiment_exhibit,
        tracking_context=tracking_context,
        tracking_finalize=tracking_finalize,
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
