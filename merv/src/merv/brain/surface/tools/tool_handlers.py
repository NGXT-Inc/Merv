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
    review_status: Any,
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
        "review.status": review_status.execute,
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
