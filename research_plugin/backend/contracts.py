"""Typed tool contracts shared by MCP and HTTP adapters."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .services.permissions import RESOURCE_ROLES, RESOURCE_TARGET_TYPES


class ContractModel(BaseModel):
    """Strict boundary model for external tool inputs."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmptyInput(ContractModel):
    pass


class ProjectScopedInput(ContractModel):
    project_id: str = Field(
        description=(
            "Explicit project scope. Project-local MCP adapters may fill this "
            "from hidden repo context before the call reaches core services."
        )
    )


class WorkflowStatusAndNextInput(ProjectScopedInput):
    experiment_id: str | None = None


class ProjectCreateInput(ContractModel):
    name: str = Field(
        description=(
            "User-confirmed project name. Do not infer a placeholder from the "
            "folder name unless the user explicitly asked for that."
        )
    )
    summary: str = Field(
        default="",
        description="Short user-confirmed project purpose or scope.",
    )
    sync_exclusions: dict[str, Any] | None = None


class ProjectUpdateInput(ProjectScopedInput):
    name: str | None = None
    summary: str | None = None
    sync_exclusions: dict[str, Any] | None = None
    reset_sync_exclusions: bool = False


class ProjectGetInput(ProjectScopedInput):
    pass


class ProjectGetSettingsInput(ProjectScopedInput):
    pass


class ProjectUpdateSettingsInput(ProjectScopedInput):
    sync_exclusions: dict[str, Any] | None = None
    reset_sync_exclusions: bool = False


class ClaimCreateInput(ProjectScopedInput):
    statement: str
    scope: str = ""
    confidence: Literal["low", "medium", "high"] = "medium"


class ClaimListInput(ProjectScopedInput):
    pass


class ClaimUpdateInput(ProjectScopedInput):
    claim_id: str
    statement: str | None = None
    scope: str | None = None
    status: Literal[
        "draft", "active", "supported", "weakened", "contradicted", "abandoned"
    ] | None = None
    confidence: Literal["low", "medium", "high"] | None = None


class ExperimentCreateInput(ProjectScopedInput):
    intent: str = Field(default="", description="Durable one-line headline for the experiment (its UI title). The full design belongs in the plan.md resource.")
    tested_claim_ids: list[str] | str | None = Field(default_factory=list)
    claim_id: str | None = Field(default=None, description="Alias for a single tested claim id.")
    claim_ids: list[str] | str | None = Field(default=None, description="Alias for tested_claim_ids.")
    title: str = Field(default="", description="Deprecated; back-compat fallback for intent. Put design detail in plan.md.")
    hypothesis: str = Field(default="", description="Deprecated; put the hypothesis in plan.md's 'Objective & hypothesis' section.")
    design: str = Field(default="", description="Deprecated; put the method in plan.md's 'Method' section.")
    success_criteria: str = Field(default="", description="Deprecated; put success criteria in plan.md's 'Evaluation' section.")
    risks: str = Field(default="", description="Deprecated; put risks in plan.md's 'Risks & confounders' section.")
    status: Literal["planned"] = Field(default="planned", description="Create always starts planned.")


class ExperimentListInput(ProjectScopedInput):
    pass


class ExperimentGetStateInput(ProjectScopedInput):
    experiment_id: str


class ExperimentTransitionInput(ProjectScopedInput):
    experiment_id: str
    transition: Literal[
        "submit_design",
        "mark_ready_to_run",
        "start_running",
        "submit_results",
        "complete",
        "abandon",
        "mark_failed",
    ]
    evidence: dict[str, Any] | None = None


class ResourceRegisterFileInput(ProjectScopedInput):
    path: str = Field(description="Repo-relative file path.")
    kind: str = "other"
    title: str = ""
    created_by: str = "codex"


class ResourceObserveFileInput(ProjectScopedInput):
    path: str


class ResourceSyncChangedFilesInput(ProjectScopedInput):
    paths: list[str]


class ResourceAssociateInput(ProjectScopedInput):
    resource_id: str
    target_type: str = Field(json_schema_extra={"enum": sorted(RESOURCE_TARGET_TYPES)})
    target_id: str
    role: str = Field(
        description="Resource association role. Use 'result' for experiment output files.",
        json_schema_extra={"enum": sorted(RESOURCE_ROLES)},
    )


class ResourceDeleteInput(ProjectScopedInput):
    resource_id: str


class ResourceListInput(ProjectScopedInput):
    pass


class ResourceResolveInput(ProjectScopedInput):
    resource_id: str


class ResourceHistoryInput(ProjectScopedInput):
    resource_id: str


class ReviewRequestInput(ProjectScopedInput):
    target_type: Literal["experiment"]
    target_id: str
    role: Literal["design_reviewer", "experiment_reviewer", "human", "automated_check"]
    reason: str = ""
    producer_session_id: str = "main"


class ReviewStartInput(ContractModel):
    review_request_id: str
    reviewer_capability: str
    declared_agent: str = ""
    caller_session_id: str = ""


class ReviewSubmitInput(ContractModel):
    review_session_id: str
    verdict: Literal["pass", "needs_changes", "fail"]
    notes: str = ""
    findings: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ReviewStatusInput(ProjectScopedInput):
    target_type: str
    target_id: str


class SandboxRequestInput(ProjectScopedInput):
    experiment_id: str
    gpu: str | None = Field(
        default=None,
        description="Optional GPU type (e.g. 'A100', 'H100'). Omit for a CPU-only sandbox.",
    )
    cpu: float | None = Field(
        default=None,
        description=(
            "Requested Modal CPU cores. Modal documents 1 CPU core as 2 vCPUs. "
            "Default 2 cores."
        ),
    )
    memory: int | None = Field(
        default=None,
        description="Requested sandbox memory in MiB. Default 8192.",
    )
    time_limit: int | None = Field(
        default=None,
        description="Max sandbox lifetime in seconds (60..86400). Default 3600.",
    )


class SandboxGetInput(ProjectScopedInput):
    experiment_id: str


class SandboxSyncInput(ProjectScopedInput):
    experiment_id: str


class SandboxListInput(ProjectScopedInput):
    pass


class SandboxReleaseInput(ProjectScopedInput):
    experiment_id: str


class SandboxTerminalInput(ProjectScopedInput):
    experiment_id: str
    tail: int | None = None


TOOL_INPUT_MODELS: dict[str, type[ContractModel]] = {
    "workflow.status_and_next": WorkflowStatusAndNextInput,
    "project.status_and_next": WorkflowStatusAndNextInput,
    "project.create": ProjectCreateInput,
    "project.update": ProjectUpdateInput,
    "project.get": ProjectGetInput,
    "project.get_settings": ProjectGetSettingsInput,
    "project.update_settings": ProjectUpdateSettingsInput,
    "project.current": EmptyInput,
    "project.list": EmptyInput,
    "claim.create": ClaimCreateInput,
    "claim.list": ClaimListInput,
    "claim.update": ClaimUpdateInput,
    "experiment.create": ExperimentCreateInput,
    "experiment.list": ExperimentListInput,
    "experiment.get_state": ExperimentGetStateInput,
    "experiment.transition": ExperimentTransitionInput,
    "resource.register_file": ResourceRegisterFileInput,
    "resource.observe_file": ResourceObserveFileInput,
    "resource.sync_changed_files": ResourceSyncChangedFilesInput,
    "resource.associate": ResourceAssociateInput,
    "resource.delete": ResourceDeleteInput,
    "resource.list": ResourceListInput,
    "resource.resolve": ResourceResolveInput,
    "resource.history": ResourceHistoryInput,
    "review.request": ReviewRequestInput,
    "review.start": ReviewStartInput,
    "review.submit": ReviewSubmitInput,
    "review.status": ReviewStatusInput,
    "sandbox.request": SandboxRequestInput,
    "sandbox.get": SandboxGetInput,
    "sandbox.sync": SandboxSyncInput,
    "sandbox.list": SandboxListInput,
    "sandbox.release": SandboxReleaseInput,
    "sandbox.terminal": SandboxTerminalInput,
    "sandbox.health": EmptyInput,
}

PROJECT_SCOPED_TOOL_NAMES = {
    name
    for name, model in TOOL_INPUT_MODELS.items()
    if issubclass(model, ProjectScopedInput)
}
