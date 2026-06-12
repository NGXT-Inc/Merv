"""Plane-boundary lints for the control/data split.

Phase 0 of docs/CLOUD_BACKEND_MIGRATION_PLAN.md: every tool contract carries a
plane, the three route sets partition the registry exactly, and the modules
that must stay cloud-servable do not grow local-process dependencies. The
import lints start narrow (subprocess only) and tighten as later phases carve
the seam.
"""

from __future__ import annotations

import ast
import unittest

from backend.contracts import (
    AGGREGATE_TOOL_NAMES,
    CONTROL_PLANE_TOOL_NAMES,
    DATA_PLANE_TOOL_NAMES,
    TOOL_CONTRACTS,
)
from tests.paths import SERVICES_ROOT


# The only services modules allowed to spawn local processes (ssh/rsync/
# ssh-keygen/tunnels). Everything else in services/ must stay cloud-servable.
SUBPROCESS_ALLOWED = {"sandbox_conn.py", "sandbox_dashboards.py"}


def _imports(path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "__future__":
                continue
            modules.add(node.module.split(".", 1)[0])
    return modules


class ToolPlanePartitionTest(unittest.TestCase):
    def test_every_tool_has_a_plane(self) -> None:
        for name, contract in TOOL_CONTRACTS.items():
            self.assertIn(contract.plane, {"control", "data", "aggregate"}, name)

    def test_planes_partition_the_registry(self) -> None:
        union = CONTROL_PLANE_TOOL_NAMES | DATA_PLANE_TOOL_NAMES | AGGREGATE_TOOL_NAMES
        self.assertEqual(union, set(TOOL_CONTRACTS))
        self.assertFalse(CONTROL_PLANE_TOOL_NAMES & DATA_PLANE_TOOL_NAMES)
        self.assertFalse(CONTROL_PLANE_TOOL_NAMES & AGGREGATE_TOOL_NAMES)
        self.assertFalse(DATA_PLANE_TOOL_NAMES & AGGREGATE_TOOL_NAMES)

    def test_data_and_aggregate_assignments_are_pinned(self) -> None:
        # The routing table of CLOUD_BACKEND_MIGRATION_PLAN.md §3.3. Changing
        # these is changing where a tool is served in split mode — do it in the
        # phase diff that moves the behavior, not casually.
        self.assertEqual(
            DATA_PLANE_TOOL_NAMES,
            {
                "resource.register_file",
                "resource.associate",
                "sandbox.request",
                "sandbox.sync",
            },
        )
        self.assertEqual(AGGREGATE_TOOL_NAMES, {"sandbox.health", "sandbox.get"})


class PlaneImportLintTest(unittest.TestCase):
    def test_only_sandbox_io_modules_spawn_processes(self) -> None:
        for path in sorted(SERVICES_ROOT.glob("*.py")):
            if path.name in SUBPROCESS_ALLOWED:
                continue
            with self.subTest(module=path.name):
                self.assertNotIn("subprocess", _imports(path))


if __name__ == "__main__":
    unittest.main()
