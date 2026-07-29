from __future__ import annotations

import ast
import inspect
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.support.sandbox_backend import FakeSandboxBackend
import merv.brain.sandbox.adapters.lambda_labs as lambda_backend
from merv.brain.sandbox.adapters.modal import (
    ModalSandboxBackend,
)
from merv.brain.sandbox.adapters.base import VmSshSandboxBackend
from merv.brain.sandbox.adapters import (
    build_sandbox_driver,
    sandbox_driver_inventory,
)
from merv.brain.sandbox.adapters import MultiplexingSandboxBackend
from merv.brain.sandbox.models import (
    BackendCapabilities,
    BackendUnavailableError,
    ProvisionedSandbox,
    SandboxBackendBase,
    SandboxRequest,
    SandboxTarget,
    TranscriptTail,
)
from merv.brain.sandbox.scheduler import SandboxScheduler
from tests.paths import BACKEND_ROOT, SERVICES_ROOT, SURFACE_ROOT

SANDBOX_ROOT = BACKEND_ROOT / "sandbox"


def _provider_neutral_sandbox_sources():
    return (
        path
        for path in SANDBOX_ROOT.rglob("*.py")
        if "adapters" not in path.relative_to(SANDBOX_ROOT).parts
    )


BACKEND_METHODS = (
    "acquire",
    "capabilities_for",
    "is_alive",
    "terminate",
    "read_transcript",
    "sandbox_environment",
    "health",
    "sample_metrics",
    "read_runs",
    "refresh_ssh_endpoint",
    "hardware_catalog",
    "find_sandbox_id",
    "sandbox_secrets",
    "write_secrets",
    "shutdown",
)


class MinimalBackend(SandboxBackendBase):
    capabilities = BackendCapabilities(name="minimal")

    def acquire(
        self,
        *,
        request: SandboxRequest,
        on_phase=None,
        on_created=None,
    ) -> ProvisionedSandbox:
        raise NotImplementedError

    def is_alive(self, *, sandbox_id: str) -> bool:
        return False

    def terminate(self, *, sandbox_id: str) -> bool:
        return False

    def read_transcript(
        self,
        *,
        target: SandboxTarget,
        tail: int | None = None,
    ) -> TranscriptTail:
        return TranscriptTail(data=b"", total_bytes=0)

    def sandbox_environment(self) -> dict:
        return {"available_tokens": [], "notes": []}

    def health(self) -> dict:
        return {"ok": True}


class VmLifecycleProbe(VmSshSandboxBackend):
    live_statuses = frozenset({"booting", "active"})
    ready_statuses = frozenset({"active"})
    terminal_statuses = frozenset({"terminated"})

    def __init__(
        self,
        resource: dict | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.resource = resource or {}
        self.error = error

    @property
    def config(self) -> SimpleNamespace:
        return SimpleNamespace(
            poll_timeout_seconds=1,
            poll_interval_seconds=0.001,
        )

    def _get_resource(self, sandbox_id: str) -> dict:
        if self.error:
            raise self.error
        return dict(self.resource, id=sandbox_id)

    def _resource_is_addressable(self, resource: dict) -> bool:
        return bool(resource.get("ip"))


class SandboxBackendContractTest(unittest.TestCase):
    def _daemons_for_backend(
        self, backend: SandboxBackendBase, *, force_expiry_reaper: bool = False
    ) -> SandboxScheduler:
        return SandboxScheduler(
            sweep=lambda **_kwargs: None,
            enforce_expiry=backend.capabilities.enforce_expiry,
            force_expiry_reaper=force_expiry_reaper,
        )

    def _backend_classes(self) -> list[type]:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"MERV_MODE": "control"}, clear=False
        ):
            backend_classes = [
                type(
                    build_sandbox_driver(
                        name=descriptor.name, repo_root=Path(tmp)
                    )
                )
                for descriptor in sandbox_driver_inventory()
            ]
        backend_classes.append(MultiplexingSandboxBackend)
        return backend_classes

    def test_orphan_lookup_takes_the_owner_the_row_records(self) -> None:
        # The deterministic sandbox name is derived from the EXPERIMENT, so a
        # sibling attempt on another provider answers to it too: the lookup is
        # routed by the row's owner, exactly as qualified_sandbox_id is. A
        # driver that drops the parameter turns every owner-routed cleanup into
        # a TypeError at the one moment a possibly-billing VM must be deleted.
        for backend_cls in [*self._backend_classes(), SandboxBackendBase]:
            with self.subTest(backend=backend_cls.__name__):
                self.assertIn(
                    "provider",
                    inspect.signature(backend_cls.find_sandbox_id).parameters,
                    f"{backend_cls.__name__}.find_sandbox_id ignores its row's owner",
                )

    def test_backend_classes_expose_full_contract_surface(self) -> None:
        backend_classes = self._backend_classes()
        for backend_cls in backend_classes:
            with self.subTest(backend=backend_cls.__name__):
                for method in BACKEND_METHODS:
                    self.assertTrue(
                        callable(getattr(backend_cls, method, None)),
                        f"{backend_cls.__name__}.{method} is missing",
                    )

    def test_base_optional_methods_return_sentinel_defaults(self) -> None:
        backend = MinimalBackend()
        target = SandboxTarget(sandbox_id="sb", workdir="/workspace")

        # Single-provider default: one backend serves every request.
        self.assertIs(backend.capabilities_for(provider="anything"), backend.capabilities)
        self.assertIsNone(backend.sample_metrics(target=target))
        self.assertIsNone(backend.read_runs(target=target))
        self.assertIsNone(backend.refresh_ssh_endpoint(sandbox_id="sb"))
        self.assertIsNone(backend.hardware_catalog())
        self.assertIsNone(backend.find_sandbox_id(experiment_id="exp"))
        self.assertEqual(backend.sandbox_secrets(), {})
        self.assertFalse(
            backend.write_secrets(target=target, secrets={"TOKEN": "value"})
        )
        self.assertIsNone(backend.shutdown())

    def test_vm_and_modal_share_exact_token_environment_default(self) -> None:
        backend = VmSshSandboxBackend()
        note = (
            "HF_TOKEN is available inside the sandbox for Hugging Face downloads. "
            "Do not print or write the token; use it through Hugging Face tooling."
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                backend.sandbox_environment(),
                {"available_tokens": [], "notes": []},
            )
        with mock.patch.dict(os.environ, {"HF_TOKEN": "secret"}, clear=True):
            self.assertEqual(
                backend.sandbox_environment(),
                {"available_tokens": ["HF_TOKEN"], "notes": [note]},
            )

        self.assertIs(
            VmSshSandboxBackend.sandbox_environment,
            SandboxBackendBase.sandbox_environment,
        )
        self.assertIs(
            ModalSandboxBackend.sandbox_environment,
            SandboxBackendBase.sandbox_environment,
        )
        self.assertIs(ModalSandboxBackend.shutdown, SandboxBackendBase.shutdown)
        self.assertIsNot(
            FakeSandboxBackend.sandbox_environment,
            SandboxBackendBase.sandbox_environment,
        )
        self.assertIsNot(
            MultiplexingSandboxBackend.sandbox_environment,
            SandboxBackendBase.sandbox_environment,
        )

    def test_vm_liveness_and_named_lookup_preserve_unknown_as_live(self) -> None:
        self.assertFalse(VmLifecycleProbe().is_alive(sandbox_id=""))
        self.assertTrue(
            VmLifecycleProbe({"status": "booting"}).is_alive(sandbox_id="vm")
        )
        self.assertFalse(
            VmLifecycleProbe({"status": "terminated"}).is_alive(sandbox_id="vm")
        )
        self.assertFalse(
            VmLifecycleProbe(
                error=BackendUnavailableError("gone", status=404)
            ).is_alive(sandbox_id="vm")
        )
        with self.assertRaisesRegex(BackendUnavailableError, "outage"):
            VmLifecycleProbe(
                error=BackendUnavailableError("outage", status=503)
            ).is_alive(sandbox_id="vm")

        conservative = VmLifecycleProbe()
        conservative.live_statuses = None
        self.assertTrue(
            conservative._status_is_live("provider-status-we-do-not-recognize")
        )
        self.assertEqual(
            conservative._find_named_resource_id(
                name="rp-exp",
                resources=[
                    {"id": "dead", "name": "rp-exp", "status": "terminated"},
                    {"id": "live", "name": "rp-exp", "status": "offline"},
                ],
            ),
            "live",
        )

    def test_vm_wait_requires_ready_status_and_an_address(self) -> None:
        ready = {"status": "active", "ip": "203.0.113.8"}
        self.assertEqual(
            VmLifecycleProbe(ready)._wait_for_vm(sandbox_id="vm"),
            {**ready, "id": "vm"},
        )
        with self.assertRaisesRegex(
            BackendUnavailableError, "terminal status terminated"
        ):
            VmLifecycleProbe({"status": "terminated"})._wait_for_vm(
                sandbox_id="vm"
            )

    def test_vm_delete_accepts_only_success_or_authoritative_404(self) -> None:
        def fail(status: int) -> None:
            raise BackendUnavailableError("delete failed", status=status)

        backend = VmLifecycleProbe()
        self.assertFalse(
            backend._delete_with_404(sandbox_id="", delete=lambda _id: None)
        )
        self.assertTrue(
            backend._delete_with_404(sandbox_id="vm", delete=lambda _id: None)
        )
        self.assertTrue(
            backend._delete_with_404(
                sandbox_id="vm", delete=lambda _id: fail(404)
            )
        )
        self.assertFalse(
            backend._delete_with_404(
                sandbox_id="vm", delete=lambda _id: fail(503)
            )
        )

    def test_vm_provider_dependencies_are_lazy_cached_and_retry_failures(self) -> None:
        cloud = object()
        config = SimpleNamespace(
            cloud=cloud,
            ssh_user="ubuntu",
            sandbox_data_dir="/workspace/data",
        )
        with mock.patch.object(
            lambda_backend.LambdaSandboxConfig,
            "from_env",
            side_effect=[RuntimeError("missing config"), config],
        ) as config_factory:
            backend = lambda_backend.LambdaLabsSandboxBackend()
            config_factory.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "missing config"):
                _ = backend.config
            self.assertIs(backend.config, config)
            self.assertIs(backend.config, config)
            self.assertEqual(config_factory.call_count, 2)

        built_client = object()
        with mock.patch.object(
            lambda_backend,
            "LambdaCloudClient",
            side_effect=[RuntimeError("client unavailable"), built_client],
        ) as client_factory:
            backend = lambda_backend.LambdaLabsSandboxBackend(config=config)
            client_factory.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "client unavailable"):
                _ = backend.client
            self.assertIs(backend.client, built_client)
            self.assertIs(backend.client, built_client)
            self.assertEqual(
                client_factory.call_args_list,
                [mock.call(config=cloud), mock.call(config=cloud)],
            )

        injected_client = object()
        with mock.patch.object(lambda_backend, "LambdaCloudClient") as client_factory:
            backend = lambda_backend.LambdaLabsSandboxBackend(
                config=config,
                client=injected_client,
            )
            self.assertIs(backend.config, config)
            self.assertIs(backend.client, injected_client)
            client_factory.assert_not_called()

    def test_provisioned_vm_fields_preserve_exact_values_and_order(self) -> None:
        accesses: list[str] = []

        class RecordingConfig:
            @property
            def ssh_user(self) -> str:
                accesses.append("ssh_user")
                return "ubuntu"

            @property
            def sandbox_data_dir(self) -> str:
                accesses.append("sandbox_data_dir")
                return "/workspace/data"

        backend = lambda_backend.LambdaLabsSandboxBackend(
            config=RecordingConfig(),  # type: ignore[arg-type]
            client=object(),  # type: ignore[arg-type]
        )
        fields = backend._provisioned_vm_fields(workdir="/workspace/exp_1")

        self.assertEqual(
            list(fields.items()),
            [
                ("ssh_user", "ubuntu"),
                ("workdir", "/workspace/exp_1"),
                ("volume_name", ""),
                ("sync_dir", "/workspace/exp_1"),
                ("unsynced_dir", "/workspace/data"),
                ("sandbox_data_dir", "/workspace/data"),
                ("reused", False),
            ],
        )
        self.assertEqual(
            accesses,
            ["ssh_user", "sandbox_data_dir", "sandbox_data_dir"],
        )
        self.assertEqual(
            ProvisionedSandbox(
                sandbox_id="vm-1",
                ssh_host="203.0.113.8",
                ssh_port=22,
                **fields,
                gpu="H100",
            ),
            ProvisionedSandbox(
                sandbox_id="vm-1",
                ssh_host="203.0.113.8",
                ssh_port=22,
                ssh_user="ubuntu",
                workdir="/workspace/exp_1",
                volume_name="",
                sync_dir="/workspace/exp_1",
                unsynced_dir="/workspace/data",
                sandbox_data_dir="/workspace/data",
                reused=False,
                gpu="H100",
            ),
        )

    def test_services_do_not_probe_backend_optional_methods(self) -> None:
        for path in (*SERVICES_ROOT.rglob("*.py"), *_provider_neutral_sandbox_sources()):
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("getattr(self.backend", source)
                self.assertNotIn("hasattr(self.backend", source)
                self.assertNotIn("getattr(caps", source)

        for path in (
            SURFACE_ROOT / "control" / "control_app.py",
            SURFACE_ROOT / "composition" / "control_mode.py",
        ):
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("getattr(self.execution_backend", source)

    def test_fake_backend_uses_base_catalog_default_until_selection_enabled(self) -> None:
        plain = FakeSandboxBackend()
        self.assertIsNone(plain.hardware_catalog())

        selecting = FakeSandboxBackend(requires_hardware_selection=True)
        catalog = selecting.hardware_catalog()
        self.assertIsInstance(catalog, dict)
        self.assertTrue(catalog["selection_required"])

    def test_default_capabilities_enable_reaper_gate(self) -> None:
        daemons = self._daemons_for_backend(MinimalBackend())

        with mock.patch.dict(
            os.environ,
            {"RESEARCH_PLUGIN_SANDBOX_REAPER": "1"},
        ):
            self.assertTrue(daemons._reaper_enabled())

    def test_fake_capabilities_disable_reaper_gate(self) -> None:
        daemons = self._daemons_for_backend(FakeSandboxBackend())

        with mock.patch.dict(
            os.environ,
            {"RESEARCH_PLUGIN_SANDBOX_REAPER": "1"},
        ):
            self.assertFalse(daemons._reaper_enabled())

    def test_local_mode_honors_reaper_off_switch(self) -> None:
        # Local mode (the default): the user owns their bill, so the env
        # off-switch disables the reaper even on a backend that enforces expiry.
        daemons = self._daemons_for_backend(MinimalBackend())
        with mock.patch.dict(os.environ, {"RESEARCH_PLUGIN_SANDBOX_REAPER": "0"}):
            self.assertFalse(daemons._reaper_enabled())

    def test_control_mode_ignores_reaper_off_switch(self) -> None:
        # Cost governance (cloud plan Phase 7): the cloud pays for every VM, so
        # an operator-set RESEARCH_PLUGIN_SANDBOX_REAPER=0 is IGNORED in control
        # mode. The flag is composition-injected: the control composition root
        # passes force_expiry_reaper=True instead of the daemons reading the
        # process mode from config (module-boundary fix, phase 4a).
        daemons = self._daemons_for_backend(MinimalBackend(), force_expiry_reaper=True)
        with mock.patch.dict(
            os.environ,
            {"RESEARCH_PLUGIN_SANDBOX_REAPER": "0"},
        ):
            self.assertTrue(daemons._reaper_enabled())

    def test_control_composition_forces_the_expiry_reaper(self) -> None:
        # The control composition (not the sandbox module) must compute the
        # force flag — the daemons no longer import merv.brain.surface.config.
        control_source = (SURFACE_ROOT / "composition" / "control_mode.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("force_expiry_reaper=True", control_source)
        daemons_source = (
            BACKEND_ROOT / "sandbox" / "scheduler.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("resolve_mode", daemons_source)

    def test_services_do_not_dispatch_on_provider_name_literals(self) -> None:
        provider_names = {
            descriptor.name
            for descriptor in sandbox_driver_inventory()
        }
        for path in (*SERVICES_ROOT.rglob("*.py"), *_provider_neutral_sandbox_sources()):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            string_literals = {
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            with self.subTest(path=path.name):
                self.assertTrue(provider_names.isdisjoint(string_literals))


if __name__ == "__main__":
    unittest.main()
