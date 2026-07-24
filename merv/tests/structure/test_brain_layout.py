"""Brain-only architecture laws.

Every tool is served by the control brain, control modules stay free of
checkout-local I/O, and the state store does not know where a repository lives.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Protocol, get_type_hints

from merv.brain.surface.tools.contracts import (
    CONTROL_PLANE_TOOL_NAMES,
    DATA_PLANE_TOOL_NAMES,
    TOOL_CONTRACTS,
    TOOL_PLANE_REGISTRY,
)
from tests.paths import (
    ARTIFACTS_ROOT,
    BACKEND_ROOT,
    DOMAIN_ROOT,
    FEED_ROOT,
    IMPORT_ROOT,
    PORTS_ROOT,
    RESEARCH_CORE_ROOT,
    SERVICES_ROOT,
    SURFACE_ROOT,
)


# Service-shaped glue that must remain cloud-safe and process-free.
GLUE_SERVICE_FILES = (
    *(SERVICES_ROOT / name for name in ("auth.py", "identity.py", "permissions.py")),
    BACKEND_ROOT / "application" / "maintenance.py",
)

# Record halves that must be servable from a cloud control plane: no local
# processes, no conn machinery, no dataplane worker.
ARTIFACTS_MODULES = tuple(sorted(ARTIFACTS_ROOT.glob("*.py")))
DOMAIN_MODULES = tuple(sorted(DOMAIN_ROOT.glob("*.py")))
PORT_MODULES = tuple(sorted(PORTS_ROOT.glob("*.py")))

CONTROL_MODULES = (
    *ARTIFACTS_MODULES,
    *DOMAIN_MODULES,
    *PORT_MODULES,
    BACKEND_ROOT / "sandbox" / "sandbox_backend.py",
    BACKEND_ROOT / "sandbox" / "sandbox_paths.py",
    SURFACE_ROOT / "tools" / "tool_facade.py",
    SURFACE_ROOT / "tools" / "tool_handlers.py",
    RESEARCH_CORE_ROOT / "projects.py",
    RESEARCH_CORE_ROOT / "claims.py",
    RESEARCH_CORE_ROOT / "experiments.py",
    RESEARCH_CORE_ROOT / "reflections.py",
    RESEARCH_CORE_ROOT / "reviews.py",
    RESEARCH_CORE_ROOT / "literature.py",
    BACKEND_ROOT / "application" / "status_guidance.py",
    RESEARCH_CORE_ROOT / "snapshots.py",
    BACKEND_ROOT / "application" / "experiments" / "presentation.py",
    SERVICES_ROOT / "permissions.py",
    BACKEND_ROOT / "application" / "workflow.py",
    BACKEND_ROOT / "sandbox" / "facade.py",
    FEED_ROOT / "feed.py",
    FEED_ROOT / "feed_policy.py",
    BACKEND_ROOT / "sandbox" / "sandbox_metrics.py",
    SURFACE_ROOT / "control" / "record_core.py",
    SURFACE_ROOT / "control" / "control_app.py",
    SURFACE_ROOT / "control" / "control_runtime.py",
    SURFACE_ROOT / "control" / "control_client.py",
    BACKEND_ROOT / "kernel" / "state" / "store.py",
    BACKEND_ROOT / "kernel" / "state" / "dialects.py",
    BACKEND_ROOT / "sandbox" / "managed_mgmt_keys.py",
)

# Module names (any dotted segment) control modules may never import.
CONTROL_FORBIDDEN_SEGMENTS = {
    "dataplane",
    "proxy",
    "sandbox_conn",
    "subprocess",
    "workspace",
}


def _imports(path: Path) -> set[str]:
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


def _import_segments(path: Path) -> set[str]:
    """Every dotted segment of every imported module path.

    Catches relative submodule imports that a top-level-only collector would
    report by parent package only.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    segments: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                segments.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            if node.module:
                segments.update(node.module.split("."))
            for alias in node.names:
                segments.update(alias.name.split("."))
    return segments


def _class_method_names(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                item.name for item in node.body if isinstance(item, ast.FunctionDef)
            }
    raise AssertionError(f"{class_name} not found in {path}")


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _call_name(node.value)
        return f"{owner}.{node.attr}" if owner else node.attr
    return ""


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".", 1)[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _resolve_call_name(name: str, aliases: dict[str, str]) -> str:
    if not name:
        return ""
    parts = name.split(".", 1)
    head = aliases.get(parts[0], parts[0])
    return f"{head}.{parts[1]}" if len(parts) == 2 else head


def _literal_args(node: ast.Call) -> list[str]:
    values: list[str] = []
    for arg in node.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            values.append(arg.value)
    return values




def _process_spawn_references(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = _import_aliases(tree)
    references: set[str] = set()
    spawn_calls = {
        "asyncio.create_subprocess_exec",
        "asyncio.create_subprocess_shell",
        "os.execl",
        "os.execle",
        "os.execlp",
        "os.execlpe",
        "os.execv",
        "os.execve",
        "os.execvp",
        "os.execvpe",
        "os.popen",
        "os.posix_spawn",
        "os.posix_spawnp",
        "os.spawnl",
        "os.spawnle",
        "os.spawnlp",
        "os.spawnlpe",
        "os.spawnv",
        "os.spawnve",
        "os.spawnvp",
        "os.spawnvpe",
        "os.system",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "subprocess.Popen",
        "subprocess.run",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    references.add("import subprocess")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "subprocess":
                references.add("from subprocess import ...")
        elif isinstance(node, ast.Call):
            name = _resolve_call_name(_call_name(node.func), aliases)
            if name in spawn_calls:
                references.add(name)
            if name == "__import__" and "subprocess" in _literal_args(node):
                references.add("__import__('subprocess')")
            if name == "importlib.import_module" and "subprocess" in _literal_args(
                node
            ):
                references.add("importlib.import_module('subprocess')")
    return references


def _imports_management_key_adapter(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.endswith("sandbox.mgmt_keys"):
                    return True
        elif isinstance(node, ast.ImportFrom) and node.module:
            module = node.module
            if module.endswith("sandbox.mgmt_keys"):
                return True
            if module.endswith("sandbox") and any(
                alias.name == "mgmt_keys" for alias in node.names
            ):
                return True
    return False




class BrainToolPlaneTest(unittest.TestCase):
    def test_every_tool_has_a_plane(self) -> None:
        self.assertEqual(set(TOOL_PLANE_REGISTRY), set(TOOL_CONTRACTS))
        for name, plane in TOOL_PLANE_REGISTRY.items():
            self.assertEqual(plane, "control", name)

    def test_every_tool_is_control(self) -> None:
        self.assertEqual(DATA_PLANE_TOOL_NAMES, frozenset())
        self.assertEqual(CONTROL_PLANE_TOOL_NAMES, frozenset(TOOL_CONTRACTS))



class PlaneImportLintTest(unittest.TestCase):
    def test_process_spawn_lint_catches_alias_forms(self) -> None:
        source = """
import os as ops
from os import system as run_cmd
from asyncio import create_subprocess_exec
from importlib import import_module as load

ops.popen("cmd")
run_cmd("cmd")
create_subprocess_exec("cmd")
load("subprocess")
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "service.py"
            path.write_text(source, encoding="utf-8")
            self.assertEqual(
                _process_spawn_references(path),
                {
                    "os.popen",
                    "os.system",
                    "asyncio.create_subprocess_exec",
                    "importlib.import_module('subprocess')",
                },
            )

    def test_management_key_adapter_lint_catches_import_forms(self) -> None:
        cases = (
            "import merv.brain.sandbox.mgmt_keys\n",
            "from merv.brain.sandbox import mgmt_keys\n",
            "from ..sandbox.mgmt_keys import LocalMgmtKeyStore\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            for index, source in enumerate(cases):
                path = Path(tmp) / f"service_{index}.py"
                path.write_text(source, encoding="utf-8")
                with self.subTest(source=source.strip()):
                    self.assertTrue(_imports_management_key_adapter(path))

    def test_only_sandbox_io_modules_spawn_processes(self) -> None:
        # Everything service-shaped is spawn-free; inside the sandbox module
        # only execution/ (provider IO) and ssh_keys.py (keygen) may spawn.
        sandbox_record_modules = [
            path
            for path in (BACKEND_ROOT / "sandbox").glob("*.py")
            if path.name != "ssh_keys.py"
        ]
        for path in sorted(
            (
                *GLUE_SERVICE_FILES,
                *RESEARCH_CORE_ROOT.rglob("*.py"),
                *FEED_ROOT.rglob("*.py"),
                *sandbox_record_modules,
            )
        ):
            with self.subTest(module=path.name):
                self.assertFalse(
                    _process_spawn_references(path),
                    f"{path.name} references process-spawn APIs",
                )

    def test_control_modules_import_no_local_io(self) -> None:
        # Hard from Phase 3: the record halves must be provably IO-free so the
        # same code can serve from a cloud VM with no checkout, no ssh, and no
        # worker in-process.
        for path in CONTROL_MODULES:
            with self.subTest(module=path.name):
                forbidden = _import_segments(path) & CONTROL_FORBIDDEN_SEGMENTS
                self.assertFalse(
                    forbidden,
                    f"{path.name} imports local-IO modules: {sorted(forbidden)}",
                )

    def test_tool_dispatcher_uses_narrow_permission_policy(self) -> None:
        from merv.brain.surface.tools.tool_facade import ToolDispatcher, ToolPermissionPolicy

        hints = get_type_hints(ToolDispatcher.__init__)
        self.assertIs(hints["permissions"], ToolPermissionPolicy)
        self.assertIn(Protocol, ToolPermissionPolicy.__mro__)
        path = SURFACE_ROOT / "tools" / "tool_facade.py"
        source = path.read_text(encoding="utf-8")
        self.assertNotIn("permissions: Any", source)
        self.assertEqual(
            _class_method_names(path, "ToolPermissionPolicy"),
            {"reject_reviewer_mutation"},
        )
        tree = ast.parse(source)
        calls = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "permissions"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
        }
        self.assertEqual(calls, {"reject_reviewer_mutation"})

    def test_state_store_knows_no_repo_root(self) -> None:
        # The record store is a records-only component (plan §3.1): local
        # checkout paths do not belong in the brain.
        source = (BACKEND_ROOT / "kernel" / "state" / "store.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("repo_root", source)

    def test_sandbox_views_do_not_import_execution(self) -> None:
        # Remote directory names are projected without provider execution machinery.
        path = BACKEND_ROOT / "sandbox" / "sandbox_views.py"
        self.assertNotIn("execution", _import_segments(path))

    def test_sandbox_services_use_backend_port_not_execution_package(self) -> None:
        # Record/control sandbox services depend on the provider-neutral port,
        # while concrete provider machinery stays under execution/.
        for name in ("sandbox_daemons.py", "sandbox_provisioner.py", "facade.py"):
            with self.subTest(module=name):
                self.assertNotIn(
                    "execution", _import_segments(BACKEND_ROOT / "sandbox" / name)
                )

    def test_sandbox_backend_port_is_neutral(self) -> None:
        imports = _import_segments(BACKEND_ROOT / "sandbox" / "sandbox_backend.py")
        forbidden = imports & {
            "dataplane",
            "execution",
            "services",
            "state",
            "subprocess",
            "workspace",
        }
        self.assertFalse(
            forbidden,
            f"sandbox backend port imports backend layers: {sorted(forbidden)}",
        )

    def test_telemetry_sinks_are_store_independent(self) -> None:
        # ActivityLogger and ToolCallStore are config-injected, machine-local
        # sinks (plan §3.2): they take explicit paths from the composition and
        # never reach into the record store.
        for name in ("activity.py", "tool_calls.py"):
            with self.subTest(module=name):
                source = (BACKEND_ROOT / "kernel" / "state" / name).read_text(
                    encoding="utf-8"
                )
                self.assertNotIn(
                    "store", _imports(BACKEND_ROOT / "kernel" / "state" / name)
                )
                self.assertNotIn("StateStore", source)

    def test_services_package_init_is_import_light(self) -> None:
        # Importing a control-safe service submodule executes services/__init__.
        # Keep the package initializer inert so a future ControlApp can import
        # individual record/view services without loading data-plane services.
        self.assertFalse(_imports(SERVICES_ROOT / "__init__.py"))

    def test_sandbox_support_is_neutral(self) -> None:
        # Shared sandbox constants/helpers stay below service and adapter code.
        imports = _import_segments(BACKEND_ROOT / "sandbox" / "sandbox_support.py")
        for forbidden in (
            "services",
            "dataplane",
            "workspace",
            "subprocess",
            "threading",
        ):
            self.assertNotIn(forbidden, imports)
    def test_brain_checkout_modules_are_absent(self) -> None:
        self.assertFalse((BACKEND_ROOT / "dataplane").exists())
        self.assertFalse((BACKEND_ROOT / "workspace.py").exists())

    def test_workflow_reads_have_explicit_application_boundaries(self) -> None:
        source = (BACKEND_ROOT / "application" / "workflow.py").read_text(
            encoding="utf-8"
        )
        imports = _import_segments(BACKEND_ROOT / "application" / "workflow.py")
        self.assertIn("facade", imports)
        self.assertIn("from .ports.sandbox import SandboxReads", source)
        self.assertNotIn("from ..sandbox.facade import SandboxReads", source)
        self.assertNotIn("from ..research_core.experiments", source)
        self.assertNotIn("reviews", imports)
        self.assertNotIn("sandboxes", imports)
        self.assertIn("snapshots: ResearchSnapshots", source)
        self.assertIn("sandboxes: SandboxReads", source)
        self.assertIn("policy: StatusGuidancePolicy", source)
        for obsolete in ("workflow.py", "workflow_views.py", "project_overview.py"):
            self.assertFalse((RESEARCH_CORE_ROOT / obsolete).exists())
        self.assertFalse((RESEARCH_CORE_ROOT / "next_action.py").exists())

    def test_submission_service_owns_association_policy(self) -> None:
        # ArtifactSubmissionService owns association policy; bytes travel over
        # the agent's own curl against token-bearer PUT routes.
        imports = _import_segments(ARTIFACTS_ROOT / "submissions.py")
        self.assertIn("association_policy", imports)
        self.assertNotIn("permissions", imports)
        source = (ARTIFACTS_ROOT / "submissions.py").read_text(encoding="utf-8")
        self.assertNotIn("permissions:", source)
        self.assertNotIn("self.permissions", source)

    def test_reflection_tools_present_research_facts_in_application(self) -> None:
        self.assertFalse((RESEARCH_CORE_ROOT / "reflection_tools.py").exists())
        facade = (RESEARCH_CORE_ROOT / "facade.py").read_text(encoding="utf-8")
        presentation = (BACKEND_ROOT / "application" / "reflections.py").read_text(
            encoding="utf-8"
        )
        app = (SURFACE_ROOT / "control" / "control_app.py").read_text(
            encoding="utf-8"
        )
        record = (SURFACE_ROOT / "control" / "record_core.py").read_text(
            encoding="utf-8"
        )
        for method in (
            "create_reflection",
            "reflection_state",
            "list_reflections",
            "transition_reflection",
        ):
            self.assertIn(f"    def {method}(", facade)
        for method in ("create", "get", "list", "transition"):
            self.assertIn(f"    def {method}(", presentation)
        self.assertIn("ReflectionCommands(reflections=self.research_core)", app)
        self.assertIn("reflection_tools=self.reflection_commands", app)
        self.assertNotIn("reflection_tools", record)

    def test_legacy_local_app_stack_is_removed(self) -> None:
        for rel in (
            "app.py",
            "local_runtime.py",
            "composition/local_mode.py",
            "surface/composition/local_mode.py",
            "dataplane/worker.py",
            "dataplane/tasks.py",
            "dataplane/state.py",
            "dataplane/sandbox_conn.py",
            "daemon/daemon_marker.py",
            "daemon/project_router.py",
            "daemon/import_tool.py",
        ):
            with self.subTest(rel=rel):
                self.assertFalse((BACKEND_ROOT / rel).exists())

    def test_control_app_uses_record_core_builder_for_record_services(self) -> None:
        app_source = (SURFACE_ROOT / "control" / "control_app.py").read_text(
            encoding="utf-8"
        )
        record_source = (SURFACE_ROOT / "control" / "record_core.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("self._record_core = build_record_core", app_source)
        for service_ctor in (
            "ClaimService(",
            "ExperimentService(",
            "FeedService(",
            "GraphRefResolver(",
            "PermissionService(",
            "ProjectService(",
            "QuotaService(",
            "ReviewService(",
            "ReflectionService(",
        ):
            self.assertNotIn(service_ctor, app_source)
            self.assertIn(service_ctor, record_source)
        for forbidden in (
            "local_runtime",
            "dataplane",
            "workspace",
            "execution",
        ):
            self.assertNotIn(
                forbidden, _import_segments(SURFACE_ROOT / "control" / "record_core.py")
            )

    def test_control_app_does_not_build_local_runtime(self) -> None:
        source = (SURFACE_ROOT / "control" / "control_app.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("class ControlApp:", source)
        self.assertIn("build_record_core", source)
        self.assertIn("build_control_tool_handlers", source)
        self.assertIn("control_tool_names = set(CONTROL_PLANE_TOOL_NAMES)", source)
        self.assertIn("available_tool_names(storage_enabled=", source)
        self.assertIn("tool_names=control_tool_names", source)
        self.assertNotIn("class ControlActivitySink", source)
        self.assertNotIn("class ControlToolCallSink", source)
        self.assertNotIn("class ControlSandboxWorker", source)
        for forbidden in (
            "TestBrain",
            "build_local_runtime",
            "build_local_tool_handlers",
            "LocalDataPlaneWorker",
            "LocalWorkspace",
            "LocalFeedImageReader",
            "ToolCallStore",
            "ActivityLogger",
        ):
            self.assertNotIn(forbidden, source)

    def test_control_mode_builds_control_app_not_local_app(self) -> None:
        path = SURFACE_ROOT / "composition" / "control_mode.py"
        source = path.read_text(encoding="utf-8")
        imports = _import_segments(path)
        self.assertIn("from ..control.control_app import ControlApp", source)
        self.assertIn("app = ControlApp(", source)
        self.assertNotIn("TestBrain", source)
        self.assertNotIn("build_local_runtime", source)
        self.assertIn("MountedMgmtKeyStore", source)
        self.assertIn("resolve_mgmt_key_path", source)
        self.assertIn("LocalMgmtKeyStore", source)
        self.assertIn("build_local_server", source)
        self.assertIn("CONTROL_COMPAT_REPO_ROOT", source)
        self.assertNotIn("tempfile", _import_segments(path))

    def test_feed_demo_uses_public_control_capabilities(self) -> None:
        source = (IMPORT_ROOT.parent / "scripts" / "_feed_demo_server.py").read_text(
            encoding="utf-8"
        )
        for removed in ("app.call_tool(", "app.feed.", "app.store."):
            self.assertNotIn(removed, source)
        self.assertIn("app.tools, app.http.feed", source)
        self.assertNotIn("app._store", source)
        self.assertIn("with store.transaction()", source)

    def test_management_key_store_is_adapter_not_service(self) -> None:
        # The service layer depends on the MgmtKeyStore port only. The local
        # filesystem key custody adapter belongs to composition-state wiring,
        # not services/.
        self.assertFalse((SERVICES_ROOT / "sandbox_mgmt_keys.py").exists())
        service_modules = (
            *GLUE_SERVICE_FILES,
            *RESEARCH_CORE_ROOT.rglob("*.py"),
            *FEED_ROOT.rglob("*.py"),
            *(
                path
                for path in (BACKEND_ROOT / "sandbox").glob("*.py")
                if path.name not in {"mgmt_keys.py", "managed_mgmt_keys.py"}
            ),
        )
        for path in sorted(service_modules):
            with self.subTest(module=path.name):
                self.assertFalse(_imports_management_key_adapter(path))
                self.assertNotIn("LocalMgmtKeyStore", path.read_text(encoding="utf-8"))
        imports = _import_segments(BACKEND_ROOT / "sandbox" / "mgmt_keys.py")
        self.assertIn("ssh_keys", imports)
        self.assertNotIn("subprocess", imports)
        self.assertNotIn("services", imports)
        self.assertIn(
            "LocalMgmtKeyStore",
            (SURFACE_ROOT / "composition" / "control_mode.py").read_text(
                encoding="utf-8"
            ),
        )
        self.assertNotIn(
            "subprocess",
            _import_segments(BACKEND_ROOT / "sandbox" / "managed_mgmt_keys.py"),
        )

    def test_local_ssh_keygen_is_single_sourced(self) -> None:
        self.assertEqual(
            _imports(BACKEND_ROOT / "sandbox" / "ssh_keys.py"),
            {"os", "pathlib", "subprocess", "kernel"},
        )
        path = BACKEND_ROOT / "sandbox" / "mgmt_keys.py"
        self.assertIn("ssh_keys", _import_segments(path))
        self.assertNotIn("subprocess", _import_segments(path))
        self.assertNotIn("ssh-keygen", path.read_text(encoding="utf-8"))

if __name__ == "__main__":
    unittest.main()
