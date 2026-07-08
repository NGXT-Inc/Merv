"""Research Map tests (RESEARCH_MAP_V1.md): deterministic append-mostly
placement, pin authority, per-register scene vocabulary, render determinism,
and the HTTP surface."""

from __future__ import annotations

import math
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.domain.map_render import (
    Viewport,
    build_scene,
    cell_label,
    entity_color,
    parse_cell,
    rasterize,
    register_for_zoom,
)
from backend.services.map import (
    CHILD_RING_BASE,
    CHILD_RING_STEP,
    DEFAULT_SEP,
    GOLDEN_ANGLE,
    ROOT_SPIRAL_C,
)
from backend.utils import NotFoundError
from tests.support.brain import TestBrain

NOW = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)


def _fixture_entities() -> list[dict]:
    """Hand-built render inputs: a fresh running experiment rooted at a claim,
    plus a stale resource — enough to exercise every register's vocabulary."""
    return [
        {
            "id": "claim_aaaa11112222", "type": "claim", "x": 0.0, "y": 0.0,
            "label": "Sparse attention preserves quality", "status": "active",
            "confidence": "medium", "text": "Sparse attention preserves quality at 90% sparsity",
            "created_at": "2026-07-06T10:00:00Z", "last_touched_at": "2026-07-07T09:00:00Z",
            "pinned": False, "is_root": True, "children_count": 3, "region_root": "claim_aaaa11112222",
        },
        {
            "id": "exp_bbbb33334444", "type": "experiment", "x": 180.0, "y": 60.0,
            "label": "sparsity_sweep", "status": "running", "attempt_index": 2,
            "text": "Sweep sparsity 50-95%", "text2": "",
            "created_at": "2026-07-06T11:00:00Z", "last_touched_at": "2026-07-07T11:30:00Z",
            "pinned": False, "is_root": False, "children_count": 0, "region_root": "claim_aaaa11112222",
        },
        {
            "id": "res_cccc55556666", "type": "resource", "x": 60.0, "y": 210.0,
            "label": "old_notes.md", "status": "note", "kind": "file", "text": "notes/old_notes.md",
            "created_at": "2026-05-01T10:00:00Z", "last_touched_at": "2026-05-02T10:00:00Z",
            "pinned": False, "is_root": False, "children_count": 0, "region_root": "claim_aaaa11112222",
        },
    ]


_EDGES = [{"src": "exp_bbbb33334444", "dst": "claim_aaaa11112222", "kind": "tests"}]


def _viewport(zoom: float) -> Viewport:
    return Viewport(cx=80.0, cy=80.0, zoom=zoom, w=900, h=600)


def _texts(scene: list[dict]) -> list[str]:
    return [op["text"] for op in scene if op["op"] == "text"]


class MapBrainTest(unittest.TestCase):
    """Service tests over the production composition."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        repo = Path(self.tmp.name)
        self.brain = TestBrain(
            repo_root=repo, db_path=repo / ".research_plugin" / "state.sqlite"
        )
        self.map = self.brain.research_map
        self.pid = self.brain.call_tool("project.create", {"name": "Map Test"})["id"]

    def _seed(self) -> dict[str, str]:
        call = self.brain.call_tool
        c1 = call("claim.create", {"project_id": self.pid, "statement": "Claim one about sparsity"})["id"]
        c2 = call("claim.create", {"project_id": self.pid, "statement": "Claim two about pruning"})["id"]
        e1 = call("experiment.create", {"project_id": self.pid, "name": "sweep_one", "intent": "Test claim one", "claim_ids": [c1]})["id"]
        e2 = call("experiment.create", {"project_id": self.pid, "name": "sweep_two", "intent": "Test claim two", "claim_ids": [c2]})["id"]
        return {"c1": c1, "c2": c2, "e1": e1, "e2": e2}

    def _positions(self) -> dict[str, tuple[float, float, str]]:
        state = self.map.state(project_id=self.pid)
        return {e["id"]: (e["x"], e["y"], e["type"]) for e in state["entities"]}

    def test_placement_is_deterministic(self) -> None:
        """Wiping the layout and re-syncing reproduces identical positions:
        placement is a pure function of the entity set and its order."""
        self._seed()
        first = self._positions()
        with self.brain.store.transaction() as conn:
            conn.execute("DELETE FROM map_layout WHERE project_id = ?", (self.pid,))
        self.assertEqual(first, self._positions())

    def test_append_only_new_entities_never_move_old_ones(self) -> None:
        ids = self._seed()
        before = self._positions()
        call = self.brain.call_tool
        c3 = call("claim.create", {"project_id": self.pid, "statement": "A third claim arrives"})["id"]
        call("experiment.create", {"project_id": self.pid, "name": "late_exp", "intent": "Test late", "claim_ids": [c3]})
        after = self._positions()
        for entity_id, position in before.items():
            self.assertEqual(after[entity_id], position, f"{entity_id} moved")
        self.assertEqual(len(after), len(before) + 2)
        # sanity: the seeded experiment sits on a ring around its claim
        ex, ey, _ = after[ids["e1"]]
        cx, cy, _ = after[ids["c1"]]
        self.assertLessEqual(
            math.hypot(ex - cx, ey - cy),
            CHILD_RING_BASE.get("experiment", 170.0) + 10 * CHILD_RING_STEP,
        )

    def test_pinned_position_is_respected_as_an_obstacle(self) -> None:
        ids = self._seed()
        positions = self._positions()
        root_count = sum(1 for _, (_x, _y, t) in positions.items() if t == "claim")
        # Pin an entity exactly onto the next root spiral slot; the next root
        # must be pushed to a different slot, and the pin itself never moves.
        k = root_count
        px = ROOT_SPIRAL_C * math.sqrt(k) * math.cos(k * GOLDEN_ANGLE)
        py = ROOT_SPIRAL_C * math.sqrt(k) * math.sin(k * GOLDEN_ANGLE)
        self.map.pin(project_id=self.pid, entity_id=ids["e2"], x=px, y=py)
        c3 = self.brain.call_tool(
            "claim.create", {"project_id": self.pid, "statement": "Root that must dodge the pin"}
        )["id"]
        after = self._positions()
        self.assertEqual(after[ids["e2"]][:2], (px, py))
        self.assertGreaterEqual(
            math.hypot(after[c3][0] - px, after[c3][1] - py), DEFAULT_SEP
        )

    def test_unpin_keeps_the_position(self) -> None:
        ids = self._seed()
        self.map.pin(project_id=self.pid, entity_id=ids["c2"], x=999.0, y=-777.0)
        version_pinned = self.map.layout_version(project_id=self.pid)
        self.map.unpin(project_id=self.pid, entity_id=ids["c2"])
        positions = self._positions()
        self.assertEqual(positions[ids["c2"]][:2], (999.0, -777.0))
        self.assertNotEqual(version_pinned, self.map.layout_version(project_id=self.pid))
        with self.assertRaises(NotFoundError):
            self.map.pin(project_id=self.pid, entity_id="exp_missing", x=0, y=0)

    def test_layout_version_changes_only_with_the_board(self) -> None:
        self._seed()
        self.map.sync(project_id=self.pid)
        v1 = self.map.layout_version(project_id=self.pid)
        self.assertEqual(v1, self.map.layout_version(project_id=self.pid))
        self.brain.call_tool("claim.create", {"project_id": self.pid, "statement": "Version bumper claim"})
        self.map.sync(project_id=self.pid)
        self.assertNotEqual(v1, self.map.layout_version(project_id=self.pid))

    def test_locate_centers_on_the_entity_at_l3(self) -> None:
        ids = self._seed()
        png, meta = self.map.locate(project_id=self.pid, entity_id=ids["e1"], now=NOW)
        positions = self._positions()
        self.assertEqual(meta["register"], "L3")
        self.assertEqual(
            (meta["viewport"]["cx"], meta["viewport"]["cy"]), positions[ids["e1"]][:2]
        )
        self.assertTrue(png.startswith(b"\x89PNG"))
        with self.assertRaises(NotFoundError):
            self.map.locate(project_id=self.pid, entity_id="exp_nope")

    def test_snapshot_bytes_are_deterministic(self) -> None:
        self._seed()
        first, _ = self.map.snapshot(project_id=self.pid, now=NOW)
        self.map._render_cache.clear()
        second, _ = self.map.snapshot(project_id=self.pid, now=NOW)
        self.assertEqual(first, second)


class SceneRegisterTest(unittest.TestCase):
    """Per-register content vocabulary, asserted on the scene (font-free)."""

    def test_registers_partition_the_zoom_axis(self) -> None:
        self.assertEqual(register_for_zoom(0.1), "L0")
        self.assertEqual(register_for_zoom(0.5), "L1")
        self.assertEqual(register_for_zoom(1.0), "L2")
        self.assertEqual(register_for_zoom(3.0), "L3")

    def test_cell_refs_round_trip(self) -> None:
        x, y = parse_cell("C4")
        self.assertEqual(cell_label(x, y), "C4")
        self.assertEqual(cell_label(0.0, 0.0), "H8")
        with self.assertRaises(ValueError):
            parse_cell("42")

    def test_l0_shows_region_names_and_no_entity_text(self) -> None:
        scene = build_scene(entities=_fixture_entities(), edges=_EDGES, viewport=_viewport(0.1), now=NOW)
        texts = _texts(scene)
        self.assertTrue(any("Sparse attention" in t for t in texts), texts)
        joined = " ".join(texts)
        # No per-entity text at L0: no ids, no experiment names. (The fixed
        # health legend legitimately spells out status words as a key.)
        self.assertNotIn("exp_bbbb33334444", joined)
        self.assertNotIn("sparsity_sweep", joined)

    def test_l0_carries_a_health_legend(self) -> None:
        texts = _texts(build_scene(entities=_fixture_entities(), edges=_EDGES, viewport=_viewport(0.1), now=NOW))
        for key in ("running", "complete", "failed", "idle / stale"):
            self.assertIn(key, texts)

    def test_l1_labels_majors_but_never_ids(self) -> None:
        scene = build_scene(entities=_fixture_entities(), edges=_EDGES, viewport=_viewport(0.5), now=NOW)
        joined = " ".join(_texts(scene))
        self.assertIn("sparsity_sweep", joined)  # running experiment = major
        self.assertNotIn("old_notes.md", joined)  # minor resource: glyph only
        self.assertNotIn("exp_bbbb33334444", joined)

    def test_l2_cards_carry_status_headline_and_short_id(self) -> None:
        scene = build_scene(entities=_fixture_entities(), edges=_EDGES, viewport=_viewport(1.2), now=NOW)
        texts = _texts(scene)
        self.assertIn("running", texts)
        # Experiment headline is the RESULT (here: attempt fallback, no metric).
        self.assertIn("attempt 2", texts)
        self.assertIn("medium", texts)   # claim confidence pill
        # A short id chip rides on the L2 card so an agent can act after two
        # snapshots, not three.
        self.assertTrue(any(t.startswith("exp_bbbb") for t in texts), texts)

    def test_l2_experiment_headline_prefers_a_result_metric(self) -> None:
        entities = [dict(e) for e in _fixture_entities()]
        exp = next(e for e in entities if e["type"] == "experiment")
        exp["text2"] = 'val_bpb 0.9788 improved over baseline {"raw": 1}'
        texts = _texts(build_scene(entities=entities, edges=_EDGES, viewport=_viewport(1.2), now=NOW))
        self.assertIn("val_bpb 0.9788", texts)
        # The raw JSON tail never reaches the card.
        self.assertFalse(any('{"raw"' in t for t in texts))

    def test_l3_shows_the_entity_id_verbatim(self) -> None:
        scene = build_scene(entities=_fixture_entities(), edges=_EDGES, viewport=_viewport(2.5), now=NOW)
        texts = _texts(scene)
        for entity_id in ("claim_aaaa11112222", "exp_bbbb33334444", "res_cccc55556666"):
            self.assertIn(entity_id, texts)
        self.assertTrue(any("Sweep sparsity" in t for t in texts))

    def test_freshness_glow_decays_and_staleness_desaturates(self) -> None:
        entities = _fixture_entities()
        scene = build_scene(entities=entities, edges=_EDGES, viewport=_viewport(0.5), now=NOW)
        glows = {op["xy"] for op in scene if op["op"] == "glow"}
        self.assertEqual(len(glows), 2)  # claim + experiment fresh; resource stale
        fresh = entity_color(entities[2] | {"last_touched_at": "2026-07-07T11:00:00Z"}, 0.5)
        stale = entity_color(entities[2], 66.0)
        self.assertNotEqual(fresh, stale)

    def test_rasterize_is_deterministic_and_sized(self) -> None:
        from PIL import Image
        import io

        scene = build_scene(entities=_fixture_entities(), edges=_EDGES, viewport=_viewport(1.2), now=NOW)
        png = rasterize(scene, 900, 600)
        self.assertEqual(png, rasterize(scene, 900, 600))
        image = Image.open(io.BytesIO(png))
        self.assertEqual(image.size, (900, 600))


class MapToolsTest(unittest.TestCase):
    """The three perceive-only MCP tools (P2): same renderer, base64 wire."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        repo = Path(self.tmp.name)
        self.brain = TestBrain(
            repo_root=repo, db_path=repo / ".research_plugin" / "state.sqlite"
        )
        self.pid = self.brain.call_tool("project.create", {"name": "Map Tools"})["id"]
        self.claim_id = self.brain.call_tool(
            "claim.create", {"project_id": self.pid, "statement": "Tool surface claim"}
        )["id"]

    def test_overview_returns_a_png_and_viewport_metadata(self) -> None:
        import base64

        result = self.brain.call_tool("map.overview", {"project_id": self.pid})
        png = base64.b64decode(result["image_png_base64"])
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(result["media_type"], "image/png")
        self.assertIn(result["register"], ("L0", "L1", "L2", "L3"))
        self.assertIn("cell='C4'", result["guidance"])

    def test_snapshot_accepts_cell_addressing(self) -> None:
        result = self.brain.call_tool("map.snapshot", {"project_id": self.pid, "cell": "H8"})
        self.assertEqual(result["register"], "L3")
        self.assertEqual(result["viewport"]["cx"], 200.0)

    def test_locate_centers_the_entity(self) -> None:
        result = self.brain.call_tool(
            "map.locate", {"project_id": self.pid, "entity_id": self.claim_id}
        )
        self.assertEqual(result["entity_id"], self.claim_id)
        self.assertEqual(result["register"], "L3")


class MapHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        repo = Path(self.tmp.name)
        self.brain = TestBrain(
            repo_root=repo, db_path=repo / ".research_plugin" / "state.sqlite"
        )
        self.client = TestClient(self.brain.fastapi_app)
        self.pid = self.brain.call_tool("project.create", {"name": "Map HTTP"})["id"]
        self.claim_id = self.brain.call_tool(
            "claim.create", {"project_id": self.pid, "statement": "HTTP surface claim"}
        )["id"]

    def test_snapshot_serves_hardened_png(self) -> None:
        response = self.client.get(f"/api/projects/{self.pid}/map/snapshot?w=640&h=480")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertTrue(response.headers["x-rp-map-layout-version"])
        self.assertTrue(response.content.startswith(b"\x89PNG"))

    def test_snapshot_accepts_cell_refs(self) -> None:
        response = self.client.get(f"/api/projects/{self.pid}/map/snapshot?cell=H8&w=640&h=480")
        self.assertEqual(response.status_code, 200)
        response = self.client.get(f"/api/projects/{self.pid}/map/snapshot?cell=99&w=640&h=480")
        self.assertEqual(response.status_code, 400)

    def test_state_exposes_positions_for_hit_testing(self) -> None:
        body = self.client.get(f"/api/projects/{self.pid}/map/state").json()
        self.assertIn("layout_version", body)
        self.assertEqual(body["entities"][0]["id"], self.claim_id)
        self.assertIn("bounds", body)
        self.assertEqual(body["registers"][0], ["L0", 0.0])

    def test_pin_roundtrip(self) -> None:
        response = self.client.post(
            f"/api/projects/{self.pid}/map/pin",
            json={"entity_id": self.claim_id, "x": 240.5, "y": -80.25},
        )
        self.assertEqual(response.status_code, 200)
        state = self.client.get(f"/api/projects/{self.pid}/map/state").json()
        entity = next(e for e in state["entities"] if e["id"] == self.claim_id)
        self.assertEqual((entity["x"], entity["y"], entity["pinned"]), (240.5, -80.25, True))
        response = self.client.post(
            f"/api/projects/{self.pid}/map/unpin", json={"entity_id": self.claim_id}
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            f"/api/projects/{self.pid}/map/pin",
            json={"entity_id": "exp_ghost", "x": 0, "y": 0},
        )
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
