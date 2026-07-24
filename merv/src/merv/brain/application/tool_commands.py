"""Application-owned decisions behind merged control-plane tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..kernel.utils import ValidationError
from ..research_core.facade import ResearchClaims, ResearchProjects
from .experiments.queries import ExperimentCollectionQuery
from .ports.storage import ObjectStorage


@dataclass(kw_only=True, slots=True)
class ControlToolOperations:
    projects: ResearchProjects
    claims: ResearchClaims
    experiments: ExperimentCollectionQuery
    storage: ObjectStorage | None

    def experiment_list(self, *, project_id: str | None = None) -> dict[str, Any]:
        return self.experiments.agent(project_id=project_id)

    def project(
        self,
        *,
        action: str,
        project_id: str = "",
        name: str = "",
        summary: str = "",
        tenant_id: str | None = None,
        user_id: str = "",
        key_project_id: str = "",
    ) -> dict[str, Any]:
        """List / current / create / overview for the calling credential."""
        if action == "list":
            return self._reachable(user_id=user_id, key_project_id=key_project_id)
        # A credential bound to one project carries that identity; one scoped
        # to the whole account has no single "current", so it gets the list.
        if action == "current":
            if not key_project_id:
                reachable = self._reachable(
                    user_id=user_id, key_project_id=key_project_id
                )
                return {
                    "exists": False,
                    "hint": (
                        "This credential reaches every project listed here, so "
                        "there is no single current project. Pass project_id "
                        "explicitly on each call."
                    ),
                    **reachable,
                }
            project = self.projects.get(project_id=key_project_id)
            return {
                "exists": True,
                "project": {
                    "id": project["id"],
                    "name": project["name"],
                    "summary": project.get("summary", ""),
                },
            }
        if action == "create":
            return self.projects.create(
                name=name, summary=summary, tenant_id=tenant_id, user_id=user_id
            )
        if action == "overview":
            # A bound key defaults to its project; anyone else must name one.
            # Fail closed rather than guess which project was meant.
            resolved = project_id or key_project_id
            if not resolved:
                raise ValidationError(
                    "project_id is required: this credential is not bound to a "
                    'single project. Call project(action="list") to see the '
                    "projects you can work in, then pass project_id explicitly.",
                    details={"field": "project_id"},
                )
            project = self.projects.get(project_id=resolved)
            return {
                "project": {
                    "id": project["id"],
                    "name": project["name"],
                    "summary": project.get("summary", ""),
                },
                "claims": self.claims.list_claims(project_id=resolved)["claims"],
                "experiments": self.experiment_list(project_id=resolved)["experiments"],
            }
        raise ValidationError(f'project action="{action}" is not recognized')

    def _reachable(self, *, user_id: str, key_project_id: str) -> dict[str, Any]:
        """Every project this caller may work in.

        ``key_project_id`` is set only for a credential confined to one
        project, and narrows the list to it. An account-scoped credential
        passes none and sees its owner's whole membership. Stashed (hidden)
        projects are omitted, matching the UI picker -- they stay reachable by
        id, so this is a decluttered view rather than the authorization edge.
        """
        listed = self.projects.list_projects(
            user_id=user_id, project_id=key_project_id
        )["projects"]
        return {
            "projects": [
                {
                    "id": project["id"],
                    "name": project["name"],
                    "summary": project.get("summary", ""),
                    "status": project.get("status", ""),
                    "created_at": project.get("created_at", ""),
                }
                for project in listed
            ]
        }

    def storage_find(
        self,
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
        assert self.storage is not None
        if object_id or name:
            return self.storage.resolve(
                project_id=project_id,
                object_id=object_id,
                name=name,
                version=version,
                include_download=include_download,
            )
        return self.storage.list_objects(
            project_id=project_id,
            kind=kind,
            status=status,
            include_expired=include_expired,
            limit=limit,
            offset=offset,
            compact=compact,
        )

    def storage_object(
        self, *, object_id: str, action: str, project_id: str | None = None
    ) -> dict[str, Any]:
        if self.storage is None or action not in {"pin", "unpin", "renew", "delete"}:
            raise ValidationError(f"unknown storage object action: {action}")
        operation = {
            "pin": self.storage.pin, "unpin": self.storage.unpin,
            "renew": self.storage.renew, "delete": self.storage.delete,
        }[action]
        return operation(project_id=project_id, object_id=object_id)
