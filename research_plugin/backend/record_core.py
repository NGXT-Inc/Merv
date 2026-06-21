"""Record-plane service composition, independent of local data-plane wiring."""

from __future__ import annotations

from dataclasses import dataclass

from .services.claims import ClaimService
from .services.experiments import ExperimentService
from .services.feed import FeedService
from .services.graph_refs import GraphRefResolver
from .services.permissions import PermissionService
from .services.project_overview import ProjectOverviewService
from .services.projects import ProjectService
from .services.quotas import QuotaService
from .services.reflection_tools import ReflectionToolService
from .services.resources import ResourceService
from .services.reviews import ReviewService
from .services.syntheses import SynthesisService
from .state import BaseStateStore
from .state.blobs import BlobStore


@dataclass(frozen=True)
class RecordCore:
    permissions: PermissionService
    quotas: QuotaService
    projects: ProjectService
    claims: ClaimService
    experiments: ExperimentService
    resources: ResourceService
    graph_refs: GraphRefResolver
    syntheses: SynthesisService
    reflections: ReflectionToolService
    project_overview: ProjectOverviewService
    reviews: ReviewService
    feed: FeedService


def build_record_core(*, store: BaseStateStore, blobs: BlobStore) -> RecordCore:
    """Build record services without workspace, worker, or execution objects."""
    permissions = PermissionService()
    quotas = QuotaService(store=store)
    projects = ProjectService(store=store)
    claims = ClaimService(store=store)
    experiments = ExperimentService(store=store, blobs=blobs)
    resources = ResourceService(store=store, permissions=permissions, blobs=blobs)
    graph_refs = GraphRefResolver(store=store)
    syntheses = SynthesisService(
        store=store,
        claims=claims,
        experiment_writer=experiments,
        project_writer=projects,
        blobs=blobs,
    )
    reflections = ReflectionToolService(syntheses=syntheses)
    project_overview = ProjectOverviewService(
        store=store,
        projects=projects,
        syntheses=syntheses,
    )
    reviews = ReviewService(
        store=store,
        permissions=permissions,
        experiments=experiments,
        syntheses=syntheses,
        blobs=blobs,
    )
    feed = FeedService(store=store, blobs=blobs)
    return RecordCore(
        permissions=permissions,
        quotas=quotas,
        projects=projects,
        claims=claims,
        experiments=experiments,
        resources=resources,
        graph_refs=graph_refs,
        syntheses=syntheses,
        reflections=reflections,
        project_overview=project_overview,
        reviews=reviews,
        feed=feed,
    )
