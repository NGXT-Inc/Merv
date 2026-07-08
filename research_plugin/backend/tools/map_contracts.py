"""MCP tool contracts for the Research Map (RESEARCH_MAP_V1.md).

Kept in the map's own module (merged into ``contracts.TOOL_CONTRACTS`` at one
seam, like the feed). Agents are PERCEIVE-ONLY in v1: the three tools return
rendered snapshots — the same pixels the human sees — and mutation stays with
the existing research tools via the entity ids readable at L3 zoom.
"""

from __future__ import annotations

from pydantic import Field

from .contracts import ProjectScopedInput, ToolContract

_SIZE_DESC = "Snapshot size in pixels (160-2400)."


class MapOverviewInput(ProjectScopedInput):
    w: int = Field(default=1200, ge=160, le=2400, description=_SIZE_DESC)
    h: int = Field(default=800, ge=160, le=2400, description=_SIZE_DESC)


class MapSnapshotInput(ProjectScopedInput):
    cx: float | None = Field(
        default=None, description="Viewport center, world x (from a prior snapshot's viewport)."
    )
    cy: float | None = Field(
        default=None, description="Viewport center, world y."
    )
    zoom: float | None = Field(
        default=None,
        description=(
            "Pixels per world unit; picks the semantic register: <0.32 L0 "
            "program shape, 0.32-0.85 L1 arcs+labels, 0.85-2 L2 entity cards, "
            ">=2 L3 full detail with entity ids. Omit everything for fit-all."
        ),
    )
    cell: str | None = Field(
        default=None,
        description=(
            "Grid cell ref from the snapshot margins (e.g. 'C4') — zooms into "
            "that cell at full detail. Alternative to cx/cy/zoom."
        ),
    )
    w: int = Field(default=1200, ge=160, le=2400, description=_SIZE_DESC)
    h: int = Field(default=800, ge=160, le=2400, description=_SIZE_DESC)


class MapLocateInput(ProjectScopedInput):
    entity_id: str = Field(
        description="Entity to center on (exp_/claim_/res_/rev_/syn_ id)."
    )
    zoom: float = Field(default=2.2, gt=0.02, le=8.0, description="Defaults to L3 (ids legible).")
    w: int = Field(default=1200, ge=160, le=2400, description=_SIZE_DESC)
    h: int = Field(default=800, ge=160, le=2400, description=_SIZE_DESC)


MAP_TOOL_CONTRACTS: dict[str, ToolContract] = {
    "map.overview": ToolContract(
        input_model=MapOverviewInput,
        description=(
            "Render the whole research board as one image — the same living "
            "map the researcher sees. Use it to orient: region blobs show "
            "where the project is alive (fresh glow) or dead (gray, "
            "desaturated), and the margin grid refs (e.g. 'C4') are addresses "
            "for map.snapshot. Start here, then zoom into what matters."
        ),
    ),
    "map.snapshot": ToolContract(
        input_model=MapSnapshotInput,
        description=(
            "Render one viewport of the research board. Zoom is context "
            "budgeting: L0/L1 for shape and clusters, L2 for status cards, "
            "L3 for full text plus entity ids — the bridge to every other "
            "tool. Pass cell='C4' from a previous snapshot's margins, or "
            "cx/cy/zoom from its viewport metadata."
        ),
    ),
    "map.locate": ToolContract(
        input_model=MapLocateInput,
        description=(
            "Render the board centered on one entity at full detail — its "
            "neighborhood, lineage edges, reviews, and freshness at a glance. "
            "Use before mutating an entity to see its spatial context."
        ),
    ),
}
