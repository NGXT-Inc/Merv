"""Brain record-service composition, independent of HTTP transport wiring."""

from __future__ import annotations

from dataclasses import dataclass

from ...artifacts import Artifacts
from ...research_core.association_targets import AssociationTargets
from ...research_core.claims import ClaimService
from ...research_core.experiments import ExperimentService
from ...feed import FeedService
from ...research_core.graph_refs import GraphRefResolver
from ...research_core.literature import LiteratureService
from ..permissions import PermissionService
from ...research_core.projects import ProjectService
from ...research_core.reviews import ReviewService
from ...research_core.reflections import ReflectionService
from ...kernel.state import BaseStateStore
from ...kernel.ports.blob_store import EvidenceBlobStore
from ..web_preview import AllowlistedPaperPreview, NetworkWebPreview


@dataclass(frozen=True)
class RecordCore:
    permissions: PermissionService
    projects: ProjectService
    claims: ClaimService
    experiments: ExperimentService
    artifacts: Artifacts
    graph_refs: GraphRefResolver
    reflection_waves: ReflectionService
    reviews: ReviewService
    feed: FeedService
    literature: LiteratureService


def build_record_core(*, store: BaseStateStore, blobs: EvidenceBlobStore) -> RecordCore:
    """Build record services without workspace, worker, or execution objects."""
    permissions = PermissionService()
    projects = ProjectService(store=store)
    claims = ClaimService(store=store)
    # Artifacts receives the narrow Research-owned association target resolver.
    artifacts = Artifacts(
        store=store,
        blobs=blobs,
        targets=AssociationTargets(),
    )
    experiments = ExperimentService(
        store=store,
        artifacts=artifacts,
    )
    graph_refs = GraphRefResolver(store=store)
    reflection_waves = ReflectionService(
        store=store,
        claims=claims,
        experiment_writer=experiments,
        artifacts=artifacts,
    )
    reviews = ReviewService(
        store=store,
        experiments=experiments,
        reflections=reflection_waves,
        artifacts=artifacts,
    )
    feed = FeedService(store=store, blobs=blobs, web_preview=NetworkWebPreview())
    literature = LiteratureService(store=store, unfurl=AllowlistedPaperPreview())
    return RecordCore(
        permissions=permissions,
        projects=projects,
        claims=claims,
        experiments=experiments,
        artifacts=artifacts,
        graph_refs=graph_refs,
        reflection_waves=reflection_waves,
        reviews=reviews,
        feed=feed,
        literature=literature,
    )
