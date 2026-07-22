from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from merv.brain.artifacts.resources import ResourceService
from merv.brain.kernel.state.store import StateStore


class CountingStateStore(StateStore):
    def __init__(self, *, db_path: Path) -> None:
        self.statements: list[str] = []
        super().__init__(db_path=db_path)

    def connect(self):
        conn = super().connect()
        conn.set_trace_callback(self.statements.append)
        return conn


class ResourceCatalogQueryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = CountingStateStore(
            db_path=Path(self.tmp.name) / "state.sqlite"
        )
        self.resources = ResourceService(
            store=self.store, association_targets=Mock()
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, *, project_id: str, count: int) -> None:
        now = "2026-07-21T00:00:00Z"
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO projects (id, name, created_at) VALUES (?, ?, ?)",
                (project_id, project_id, now),
            )
            for index in range(count):
                resource_id = f"res_{project_id}_{index:04d}"
                version_id = f"ver_{project_id}_{index:04d}"
                path = f"files/{count - index:04d}.md"
                conn.execute(
                    """
                    INSERT INTO resources (
                      id, project_id, path, kind, title, current_version_id,
                      version_token, mtime_ns, size_bytes, observed_at,
                      created_at, updated_at
                    ) VALUES (?, ?, ?, 'note', '', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        resource_id,
                        project_id,
                        path,
                        version_id,
                        f"token-{index}",
                        index,
                        index + 1,
                        now,
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO resource_versions (
                      id, resource_id, project_id, path, content_sha256,
                      size_bytes, mtime_ns, observed_at, created_at, created_seq
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        version_id,
                        resource_id,
                        project_id,
                        path,
                        f"{index:064x}",
                        index + 1,
                        index,
                        now,
                        now,
                        index + 1,
                    ),
                )

    def _selects(self) -> list[str]:
        return [
            statement
            for statement in self.store.statements
            if statement.lstrip().upper().startswith(("SELECT", "WITH"))
        ]

    def _list(self, *, project_id: str, compact: bool = False):
        self.store.statements.clear()
        result = self.resources.list_resources(
            project_id=project_id, compact=compact
        )
        return result, self._selects()

    def test_one_and_twenty_five_full_catalogs_each_use_six_selects(self) -> None:
        for count in (1, 25):
            with self.subTest(count=count):
                project_id = f"proj_{count}"
                self._seed(project_id=project_id, count=count)

                full, full_selects = self._list(project_id=project_id)
                compact, compact_selects = self._list(
                    project_id=project_id, compact=True
                )

                self.assertEqual(len(full_selects), 6)
                self.assertEqual(len(compact_selects), 3)
                self.assertEqual(full["count"], count)
                self.assertEqual(compact["count"], count)
                self.assertEqual(
                    [item["path"] for item in full["resources"]],
                    sorted(item["path"] for item in full["resources"]),
                )
                self.assertTrue(
                    all(item["current_version"] for item in full["resources"])
                )
                self.assertTrue(
                    all(
                        list(item) == list(self.resources._COMPACT_FIELDS)
                        for item in compact["resources"]
                    )
                )

    def test_list_matches_resolve_and_preserves_cross_version_associations(self) -> None:
        project_id = "proj_shape"
        self._seed(project_id=project_id, count=2)
        now = "2026-07-21T00:01:00Z"
        resource_a = "res_proj_shape_0000"
        resource_b = "res_proj_shape_0001"
        version_a = "ver_proj_shape_0000"
        version_b = "ver_proj_shape_0001"
        with self.store.transaction() as conn:
            for association_id, resource_id, version_id, role, target_id in (
                ("assoc_current", resource_a, version_a, "plan", "exp_1"),
                ("assoc_cross", resource_b, version_a, "report", "exp_2"),
                ("assoc_b", resource_b, version_b, "input", "exp_3"),
            ):
                conn.execute(
                    """
                    INSERT INTO resource_associations (
                      id, resource_id, version_id, target_type, target_id,
                      role, attempt_index, created_at, created_seq
                    ) VALUES (?, ?, ?, 'experiment', ?, ?, 1, ?, 1)
                    """,
                    (
                        association_id,
                        resource_id,
                        version_id,
                        target_id,
                        role,
                        now,
                    ),
                )

        listed = self.resources.list_resources(project_id=project_id)["resources"]
        by_id = {str(item["id"]): item for item in listed}

        for resource_id in (resource_a, resource_b):
            self.assertEqual(
                by_id[resource_id],
                self.resources.resolve(
                    project_id=project_id, resource_id=resource_id
                ),
            )
        self.assertEqual(
            [item["role"] for item in by_id[resource_a]["associations"]],
            ["plan"],
        )
        self.assertEqual(
            [
                item["role"]
                for item in by_id[resource_a]["current_version"]["associations"]
            ],
            ["plan", "report"],
        )
        self.assertEqual(
            [item["role"] for item in by_id[resource_b]["associations"]],
            ["input", "report"],
        )
        self.assertEqual(
            [
                item["role"]
                for item in by_id[resource_b]["current_version"]["associations"]
            ],
            ["input"],
        )
        self.assertEqual(
            list(by_id[resource_a]["associations"][0]),
            ["target_type", "target_id", "role", "attempt_index", "version_id"],
        )
        self.assertEqual(
            list(by_id[resource_a]["current_version"]["associations"][0]),
            ["target_type", "target_id", "role", "attempt_index", "created_at"],
        )

    def test_four_hundred_one_resources_use_bounded_deduplicated_batches(self) -> None:
        project_id = "proj_401"
        self._seed(project_id=project_id, count=401)
        shared_version = "ver_proj_401_0000"
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE resources SET current_version_id = ? WHERE id = ?",
                (shared_version, "res_proj_401_0400"),
            )
            conn.execute(
                """
                INSERT INTO resource_associations (
                  id, resource_id, version_id, target_type, target_id, role,
                  attempt_index, created_at, created_seq
                ) VALUES ('assoc_shared', 'res_proj_401_0000', ?,
                          'experiment', 'exp_1', 'plan', 1,
                          '2026-07-21T00:01:00Z', 1)
                """,
                (shared_version,),
            )

        result, selects = self._list(project_id=project_id)

        self.assertEqual(result["count"], 401)
        self.assertEqual(len(selects), 7)
        by_id = {str(item["id"]): item for item in result["resources"]}
        for resource_id in ("res_proj_401_0000", "res_proj_401_0400"):
            self.assertEqual(
                [
                    item["role"]
                    for item in by_id[resource_id]["current_version"]["associations"]
                ],
                ["plan"],
            )


if __name__ == "__main__":
    unittest.main()
