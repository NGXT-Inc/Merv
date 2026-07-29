"""MultiplexingSandboxBackend: routing, id prefixes, merged catalogs."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from merv.brain.sandbox.execution import build_sandbox_backend
from merv.brain.sandbox.execution.backends.fake import FakeSandboxBackend
from merv.brain.sandbox.execution.multiplexer import MultiplexingSandboxBackend
from merv.brain.sandbox.sandbox_backend import (
    BackendCapabilities,
    BackendUnavailableError,
    BackendValidationError,
    SandboxRequest,
)


def _request(**overrides) -> SandboxRequest:
    fields = {
        "experiment_id": "exp_1",
        "project_id": "proj_1",
        "public_key": "ssh-ed25519 AAAA test",
    }
    fields.update(overrides)
    return SandboxRequest(**fields)


class MultiplexerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.alpha = FakeSandboxBackend()
        self.alpha.capabilities = BackendCapabilities(
            name="alpha", enforce_expiry=False, lifetime_extension_supported=True
        )
        self.beta = FakeSandboxBackend(requires_hardware_selection=True)
        self.beta.capabilities = BackendCapabilities(
            name="beta",
            enforce_expiry=True,
            lifetime_extension_supported=False,
            requires_hardware_selection=True,
            configurable_resources=False,
        )
        self.mux = MultiplexingSandboxBackend(
            backends={"alpha": self.alpha, "beta": self.beta},
            default="alpha",
            aliases={"beta_alias": "beta"},
        )

    # ---- routing + prefixes ----

    def test_acquire_routes_by_request_provider_and_prefixes_id(self) -> None:
        provisioned = self.mux.acquire(request=_request(provider="beta"))

        self.assertTrue(provisioned.sandbox_id.startswith("beta:"))
        self.assertEqual(len(self.beta.acquired), 1)
        self.assertEqual(len(self.alpha.acquired), 0)

    def test_acquire_defaults_and_resolves_aliases(self) -> None:
        default = self.mux.acquire(request=_request())
        aliased = self.mux.acquire(request=_request(provider="beta_alias"))

        self.assertTrue(default.sandbox_id.startswith("alpha:"))
        self.assertTrue(aliased.sandbox_id.startswith("beta:"))

    def test_acquire_unknown_provider_lists_configured(self) -> None:
        with self.assertRaisesRegex(BackendValidationError, "alpha, beta"):
            self.mux.acquire(request=_request(provider="nope"))

    def test_on_created_receives_prefixed_id(self) -> None:
        created: list[str] = []
        self.mux.acquire(
            request=_request(provider="beta"),
            on_created=lambda sid, _name: created.append(sid),
        )

        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].startswith("beta:"))

    def test_id_addressed_calls_round_trip_through_owner(self) -> None:
        provisioned = self.mux.acquire(request=_request(provider="beta"))
        native = provisioned.sandbox_id.split(":", 1)[1]

        self.assertTrue(self.mux.is_alive(sandbox_id=provisioned.sandbox_id))
        self.assertTrue(self.mux.terminate(sandbox_id=provisioned.sandbox_id))
        self.assertIn(native, self.beta.terminated)
        self.assertNotIn(native, self.alpha.terminated)
        self.assertFalse(self.mux.is_alive(sandbox_id=provisioned.sandbox_id))

    def test_unprefixed_id_routes_to_default_backend(self) -> None:
        provisioned = self.alpha.acquire(request=_request())  # legacy: no prefix

        self.assertTrue(self.mux.is_alive(sandbox_id=provisioned.sandbox_id))
        self.assertTrue(self.mux.terminate(sandbox_id=provisioned.sandbox_id))
        self.assertIn(provisioned.sandbox_id, self.alpha.terminated)

    def test_unknown_prefix_raises_instead_of_answering(self) -> None:
        # A wrong-provider 404 would read as "gone" and get a live VM's row
        # marked terminated; an unconfigured prefix must never be answered.
        with self.assertRaises(BackendUnavailableError):
            self.mux.is_alive(sandbox_id="gamma:sb-1")
        with self.assertRaises(BackendUnavailableError):
            self.mux.terminate(sandbox_id="gamma:sb-1")

    # ---- legacy ids are owned by the ROW, not by the current default ----

    def test_a_legacy_id_is_addressed_to_the_provider_its_row_records(self) -> None:
        # Un-prefixed id + a row that says "beta". Routing it to the default
        # (alpha) would 404, read as "gone", and strand a live beta VM.
        provisioned = self.beta.acquire(request=_request())  # native, no prefix

        addressed = self.mux.qualified_sandbox_id(
            sandbox_id=provisioned.sandbox_id, provider="beta"
        )

        self.assertEqual(addressed, f"beta:{provisioned.sandbox_id}")
        self.assertTrue(self.mux.is_alive(sandbox_id=addressed))
        self.assertTrue(self.mux.terminate(sandbox_id=addressed))
        self.assertIn(provisioned.sandbox_id, self.beta.terminated)
        self.assertNotIn(provisioned.sandbox_id, self.alpha.terminated)

    def test_a_legacy_id_resolves_its_rows_provider_alias(self) -> None:
        self.assertEqual(
            self.mux.qualified_sandbox_id(sandbox_id="sb-7", provider="beta_alias"),
            "beta:sb-7",
        )

    def test_a_row_naming_an_unconfigured_provider_refuses_to_be_addressed(self) -> None:
        # gamma was dropped from MERV_EXECUTION_BACKENDS. Nobody left can
        # answer for its ids, and a guessed answer risks a billing VM.
        with self.assertRaises(BackendUnavailableError):
            self.mux.qualified_sandbox_id(sandbox_id="sb-7", provider="gamma")
        with self.assertRaises(BackendUnavailableError):
            self.mux.qualified_sandbox_id(sandbox_id="gamma:sb-7")

    def test_an_id_that_already_names_its_owner_is_left_alone(self) -> None:
        self.assertEqual(
            self.mux.qualified_sandbox_id(sandbox_id="beta:sb-7", provider="beta"),
            "beta:sb-7",
        )
        # No recorded provider at all: pre-multiplexer behavior, the default.
        self.assertEqual(self.mux.qualified_sandbox_id(sandbox_id="sb-7"), "sb-7")

    def test_find_sandbox_id_returns_prefixed_hit(self) -> None:
        self.beta.acquire(request=_request(sandbox_uid="uid_beta"))

        found = self.mux.find_sandbox_id(experiment_id="exp_1", sandbox_uid="uid_beta")

        self.assertIsNotNone(found)
        self.assertTrue(found.startswith("beta:"))

    def _name_orphan(self, backend: FakeSandboxBackend, sandbox_id: str) -> None:
        """A live VM on `backend` answering to the experiment-derived name."""
        backend.by_experiment["exp_1"] = sandbox_id
        backend.alive[sandbox_id] = True

    def _watch_lookups(self, backend: FakeSandboxBackend) -> list[str]:
        asked: list[str] = []
        inner = backend.find_sandbox_id

        def find_sandbox_id(*, experiment_id: str, sandbox_uid: str = "", **_kw):
            asked.append(sandbox_uid or experiment_id)
            return inner(experiment_id=experiment_id, sandbox_uid=sandbox_uid)

        backend.find_sandbox_id = find_sandbox_id  # type: ignore[method-assign]
        return asked

    def test_find_sandbox_id_asks_only_the_provider_the_row_records(self) -> None:
        # Both providers hold a VM answering to the same experiment-derived
        # name — the sibling-attempt case. Taking the first fleet-wide hit
        # would hand the caller alpha's id for a beta-owned row: alpha's VM
        # gets terminated and its answer then classifies beta's row as gone.
        self._name_orphan(self.alpha, "sb-alpha-sibling")
        self._name_orphan(self.beta, "sb-beta-own")
        asked_alpha = self._watch_lookups(self.alpha)

        found = self.mux.find_sandbox_id(experiment_id="exp_1", provider="beta")

        self.assertEqual(found, "beta:sb-beta-own")
        self.assertEqual(asked_alpha, [], "the wrong provider was asked anyway")

    def test_find_sandbox_id_resolves_the_recorded_owners_alias(self) -> None:
        self._name_orphan(self.alpha, "sb-alpha-sibling")
        self._name_orphan(self.beta, "sb-beta-own")

        self.assertEqual(
            self.mux.find_sandbox_id(experiment_id="exp_1", provider="beta_alias"),
            "beta:sb-beta-own",
        )

    def test_find_sandbox_id_refuses_a_recorded_owner_nobody_can_reach(self) -> None:
        # gamma left MERV_EXECUTION_BACKENDS. alpha and beta saying "not mine"
        # is not evidence, and alpha's same-named VM is certainly not gamma's.
        self._name_orphan(self.alpha, "sb-alpha-sibling")
        asked_alpha = self._watch_lookups(self.alpha)

        with self.assertRaises(BackendUnavailableError):
            self.mux.find_sandbox_id(experiment_id="exp_1", provider="gamma")

        self.assertEqual(asked_alpha, [])

    def test_find_sandbox_id_still_fans_out_for_a_row_with_no_owner(self) -> None:
        # A pre-multiplexer row names nobody, so the fleet-wide sweep is all
        # there is — and there the first hit is the only hit.
        self._name_orphan(self.beta, "sb-beta-own")

        self.assertEqual(
            self.mux.find_sandbox_id(experiment_id="exp_1"), "beta:sb-beta-own"
        )

    def test_write_secrets_routes_by_prefix(self) -> None:
        calls: list[str] = []
        self.beta.write_secrets = (  # type: ignore[method-assign]
            lambda *, sandbox_id, secrets, **_kw: calls.append(sandbox_id) or True
        )

        self.assertTrue(
            self.mux.write_secrets(sandbox_id="beta:sb-9", secrets={"T": "v"})
        )
        self.assertEqual(calls, ["sb-9"])

    # ---- capabilities ----

    def test_capabilities_for_resolves_per_provider(self) -> None:
        self.assertEqual(self.mux.capabilities_for(provider=None).name, "alpha")
        self.assertEqual(self.mux.capabilities_for(provider="beta").name, "beta")
        self.assertEqual(self.mux.capabilities_for(provider="beta_alias").name, "beta")
        with self.assertRaises(BackendValidationError):
            self.mux.capabilities_for(provider="nope")

    def test_aggregate_capabilities_mirror_default_with_expiry_union(self) -> None:
        caps = self.mux.capabilities

        self.assertEqual(caps.name, "alpha")
        self.assertTrue(caps.configurable_resources)
        # Billing protection wins: any enforcing backend makes the fleet enforce.
        self.assertTrue(caps.enforce_expiry)

    # ---- merged catalog ----

    def test_hardware_catalog_merges_and_tags_options_with_provider(self) -> None:
        catalog = self.mux.hardware_catalog()

        self.assertIsNotNone(catalog)
        self.assertEqual(catalog["provider"], "alpha")
        self.assertEqual(catalog["providers"], ["beta"])  # alpha has no catalog
        self.assertTrue(catalog["options"])
        self.assertTrue(all(o["provider"] == "beta" for o in catalog["options"]))
        prices = [o["price_usd_per_hour"] for o in catalog["options"]]
        self.assertEqual(prices, sorted(prices))

    def test_health_aggregates_and_names_failures(self) -> None:
        self.beta.healthy = False

        health = self.mux.health()

        self.assertFalse(health["ok"])
        self.assertIn("beta", health["error"])
        self.assertTrue(health["backends"]["alpha"]["ok"])

    def test_environment_and_secrets_merge_across_backends(self) -> None:
        # Post-Phase-C the facade threads the provisioning user's HF token; the
        # multiplexer forwards it to each backend.
        self.alpha.sandbox_secrets = lambda *, hf_token="": {"A": hf_token or "1"}  # type: ignore[method-assign]
        self.beta.sandbox_secrets = lambda *, hf_token="": {"B": "2"}  # type: ignore[method-assign]

        self.assertEqual(
            self.mux.sandbox_secrets(hf_token="hf_x"), {"A": "hf_x", "B": "2"}
        )
        self.assertEqual(self.mux.sandbox_secrets(), {"A": "1", "B": "2"})
        self.assertEqual(
            self.mux.sandbox_environment(), {"available_tokens": [], "notes": []}
        )


class MultiplexedServiceTest(unittest.TestCase):
    """SandboxEngine over a two-provider multiplexer (whole-stack routing)."""

    def setUp(self) -> None:
        from merv.brain.mlflow import CentralMlflowService
        from tests.support.brain import TestBrain

        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.alpha = FakeSandboxBackend()
        self.alpha.capabilities = BackendCapabilities(
            name="alpha", enforce_expiry=False, lifetime_extension_supported=True
        )
        self.beta = FakeSandboxBackend(requires_hardware_selection=True)
        self.beta.capabilities = BackendCapabilities(
            name="beta",
            enforce_expiry=False,
            lifetime_extension_supported=True,
            requires_hardware_selection=True,
            configurable_resources=False,
        )
        self.mux = MultiplexingSandboxBackend(
            backends={"alpha": self.alpha, "beta": self.beta}, default="alpha"
        )
        self.app = TestBrain(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
            execution_backend=self.mux,
            mlflow_tracking=CentralMlflowService(
                mode="external",
                tracking_uri="https://mlflow.test",
                health_check=lambda: True,
            ),
        )
        self.project_id = self.app.call_tool(
            "project", {"action": "create", "name": "Mux Project"}
        )["id"]

    def tearDown(self) -> None:
        self.app.shutdown()
        self.tmp.cleanup()

    def call(self, tool: str, **kwargs):
        return self.app.call_tool(tool, kwargs)

    def test_request_routes_to_named_provider_and_records_it(self) -> None:
        result = self.call(
            "sandbox.request",
            project_id=self.project_id,
            provider="beta",
            instance_type="gpu_1x_a10",
        )

        self.assertEqual(result["status"], "running")
        self.assertTrue(result["sandbox_id"].startswith("beta:"))
        self.assertEqual(result["provider"], "beta")
        self.assertEqual(len(self.beta.acquired), 1)
        self.assertEqual(len(self.alpha.acquired), 0)
        with self.app.store.transaction() as conn:
            generation = conn.execute(
                "SELECT provider, price_usd_per_hour FROM sandbox_generations"
            ).fetchone()
        self.assertEqual(generation["provider"], "beta")
        self.assertEqual(generation["price_usd_per_hour"], 0.75)

    def test_default_provider_serves_when_omitted(self) -> None:
        result = self.call("sandbox.request", project_id=self.project_id)

        self.assertEqual(result["status"], "running")
        self.assertTrue(result["sandbox_id"].startswith("alpha:"))
        self.assertEqual(result["provider"], "alpha")

    def test_unknown_provider_is_a_clean_validation_error(self) -> None:
        from merv.brain.kernel.utils import ValidationError

        with self.assertRaisesRegex(ValidationError, "alpha, beta"):
            self.call("sandbox.request", project_id=self.project_id, provider="gamma")

    def test_selection_gate_keys_on_the_requested_provider(self) -> None:
        # beta bundles hardware: no instance_type => the merged selection menu.
        result = self.call(
            "sandbox.request", project_id=self.project_id, provider="beta"
        )

        self.assertEqual(result["status"], "needs_selection")
        self.assertTrue(
            all(o["provider"] == "beta" for o in result["options"])
        )

    def test_release_of_prefixed_sandbox_terminates_owner_vm(self) -> None:
        created = self.call(
            "sandbox.request",
            project_id=self.project_id,
            provider="beta",
            instance_type="gpu_1x_a10",
        )
        native = created["sandbox_id"].split(":", 1)[1]

        released = self.call(
            "sandbox.release",
            project_id=self.project_id,
            sandbox_uid=created["sandbox_uid"],
            confirm_retained=True,
        )

        self.assertEqual(released["status"], "terminated")
        self.assertIn(native, self.beta.terminated)
        self.assertNotIn(native, self.alpha.terminated)

    def test_options_returns_merged_provider_tagged_menu(self) -> None:
        result = self.call("sandbox.options", project_id=self.project_id)

        self.assertEqual(result["backend"], "alpha")
        self.assertEqual(result["providers"], ["beta"])
        self.assertTrue(all(o["provider"] == "beta" for o in result["options"]))


class LegacyRowLivenessRoutingTest(unittest.TestCase):
    """Every liveness call site asks the provider the ROW records (SAN-06).

    The rows under test predate the multiplexer, so their ``sandbox_id`` carries
    no provider prefix and cannot name its own owner. The deployment's default
    has since changed to alpha while the row still says beta. Routing those ids
    to alpha gets a truthful "not mine" — which, read as "the VM is gone",
    terminalizes the row and leaves a live beta VM billing forever behind a
    status no sweep ever revisits.
    """

    def setUp(self) -> None:
        from merv.brain.mlflow import CentralMlflowService
        from tests.support.brain import TestBrain

        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.alpha = FakeSandboxBackend()
        self.alpha.capabilities = BackendCapabilities(name="alpha", enforce_expiry=False)
        self.beta = FakeSandboxBackend()
        self.beta.capabilities = BackendCapabilities(name="beta", enforce_expiry=False)
        # alpha is today's default; beta served the rows below.
        self.mux = MultiplexingSandboxBackend(
            backends={"alpha": self.alpha, "beta": self.beta}, default="alpha"
        )
        self.app = TestBrain(
            repo_root=self.repo,
            db_path=self.repo / ".research_plugin" / "state.sqlite",
            execution_backend=self.mux,
            mlflow_tracking=CentralMlflowService(
                mode="external",
                tracking_uri="https://mlflow.test",
                health_check=lambda: True,
            ),
        )
        self.project_id = self.app.call_tool(
            "project", {"action": "create", "name": "Legacy Rows"}
        )["id"]

    def tearDown(self) -> None:
        self.app.shutdown()
        self.tmp.cleanup()

    def _experiment(self, name: str = "exp") -> str:
        return self.app.call_tool(
            "experiment.create",
            {"project_id": self.project_id, "name": name, "intent": "x"},
        )["id"]

    def _legacy_row(
        self,
        *,
        provider: str,
        sandbox_uid: str = "uid_legacy",
        sandbox_id: str = "sb-legacy",
        alive_on: FakeSandboxBackend | None = None,
    ) -> str:
        exp_id = self._experiment()
        self.app.sandbox_storage.upsert(
            experiment_id=exp_id,
            sandbox_uid=sandbox_uid,
            project_id=self.project_id,
            sandbox_id=sandbox_id,  # un-prefixed: nothing in the id names beta
            provider=provider,
            status="running",
            ssh_host="host.test",
            ssh_port=22,
            ssh_user="root",
            expires_at="2999-01-01T00:00:00Z",
        )
        if alive_on is not None:
            alive_on.alive[sandbox_id] = True
        return exp_id

    def _row(self, sandbox_uid: str = "uid_legacy") -> dict:
        return self.app.sandbox_storage.get_by_uid(sandbox_uid=sandbox_uid)

    def _watch_alpha(self) -> list[str]:
        """Record every id the WRONG provider is asked about."""
        asked: list[str] = []
        inner = self.alpha.is_alive

        def is_alive(*, sandbox_id: str) -> bool:
            asked.append(sandbox_id)
            return inner(sandbox_id=sandbox_id)

        self.alpha.is_alive = is_alive  # type: ignore[method-assign]
        return asked

    # ---- sandbox.get -> lifecycle.reconcile ----

    def test_reconcile_never_terminalizes_a_row_its_own_provider_calls_live(
        self,
    ) -> None:
        exp_id = self._legacy_row(provider="beta", alive_on=self.beta)
        asked_alpha = self._watch_alpha()
        alpha_operations: list[str] = []
        beta_operations: list[str] = []
        self.alpha.sandbox_secrets = lambda **_kwargs: {}  # type: ignore[method-assign]
        self.beta.sandbox_secrets = lambda **_kwargs: {"TOKEN": "write-only"}  # type: ignore[method-assign]
        self.alpha.write_secrets = lambda **_kwargs: alpha_operations.append("secrets") or True  # type: ignore[method-assign]
        self.beta.write_secrets = lambda **_kwargs: beta_operations.append("secrets") or True  # type: ignore[method-assign]
        self.alpha.sample_metrics = lambda **_kwargs: alpha_operations.append("metrics") or {}  # type: ignore[method-assign]
        self.beta.sample_metrics = lambda **_kwargs: beta_operations.append("metrics") or {}  # type: ignore[method-assign]
        alpha_read_runs = self.alpha.read_runs
        beta_read_runs = self.beta.read_runs
        self.alpha.read_runs = lambda **kwargs: alpha_operations.append("runs") or alpha_read_runs(**kwargs)  # type: ignore[method-assign]
        self.beta.read_runs = lambda **kwargs: beta_operations.append("runs") or beta_read_runs(**kwargs)  # type: ignore[method-assign]
        self.beta.transcripts["uid_legacy"] = "owned by beta"
        # The control that makes this bug real: today's default says "gone".
        self.assertFalse(self.alpha.alive.get("sb-legacy", False))

        view = self.app.sandboxes.get(project_id=self.project_id, experiment_id=exp_id)
        metrics = self.app.sandboxes.sample_metrics(
            project_id=self.project_id,
            experiment_id=exp_id,
        )
        self.app.sandboxes.observe_run(
            sandbox_uid="uid_legacy",
            max_age_seconds=0.0,
        )
        terminal = self.app.sandboxes.terminal(
            project_id=self.project_id,
            experiment_id=exp_id,
        )

        self.assertEqual(view["status"], "running")
        self.assertTrue(metrics["available"])
        self.assertIn("owned by beta", terminal["transcript"])
        self.assertEqual(self._row()["status"], "running")
        self.assertEqual(asked_alpha, [], "the default provider was asked anyway")
        self.assertEqual(alpha_operations, [])
        self.assertEqual(
            sorted(beta_operations), ["metrics", "runs", "secrets"]
        )
        self.assertEqual(self.alpha.transcript_reads, [])
        self.assertEqual(
            self.beta.transcript_reads[-1]["sandbox_id"], "sb-legacy"
        )
        self.assertNotIn("sb-legacy", self.beta.terminated)

    def test_reconcile_still_terminalizes_when_the_owner_says_it_is_gone(self) -> None:
        # The control: beta itself answers "not alive", which IS authoritative.
        exp_id = self._legacy_row(provider="beta")
        self.beta.alive["sb-legacy"] = False

        view = self.app.sandboxes.get(project_id=self.project_id, experiment_id=exp_id)

        self.assertEqual(view["status"], "terminated")

    # ---- sandbox.request -> reuse decision ----

    def test_request_reuses_the_legacy_row_instead_of_destroying_its_vm(self) -> None:
        from tests.support.brain import DEFAULT_PUBLIC_KEY

        exp_id = self._legacy_row(provider="beta", alive_on=self.beta)
        asked_alpha = self._watch_alpha()

        result = self.app.sandboxes.request(
            project_id=self.project_id,
            experiment_id=exp_id,
            public_key=DEFAULT_PUBLIC_KEY,
        )

        self.assertEqual(result["sandbox_uid"], "uid_legacy")
        self.assertEqual(result["status"], "running")
        self.assertEqual(asked_alpha, [])
        # Nothing was cleared for reacquisition, so beta's VM is untouched.
        self.assertNotIn("sb-legacy", self.beta.terminated)
        self.assertEqual(len(self.beta.acquired), 0)
        self.assertEqual(len(self.alpha.acquired), 0)

    # ---- sandbox.attach ----

    def test_attach_accepts_a_legacy_row_its_own_provider_calls_live(self) -> None:
        self._legacy_row(provider="beta", alive_on=self.beta)
        other = self._experiment("second")
        asked_alpha = self._watch_alpha()

        view = self.app.sandboxes.attach(
            experiment_id=other,
            project_id=self.project_id,
            sandbox_uid="uid_legacy",
        )

        self.assertEqual(view["sandbox_uid"], "uid_legacy")
        self.assertEqual(asked_alpha, [])
        self.assertEqual(self._row()["status"], "running")

    # ---- cleanup of a row with no recorded id ----

    def test_cleanup_never_destroys_a_same_named_attempt_on_another_provider(
        self,
    ) -> None:
        # The worst shape this bug takes: a beta row that never recorded an id,
        # so cleanup falls back to the experiment-derived deterministic name —
        # a name an alpha sibling under the same experiment answers to as well.
        # Searching the fleet destroys alpha's VM and then reads alpha's answer
        # as proof beta's sandbox is gone: the wrong attempt killed, the real
        # one still billing behind a terminalized row.
        exp_id = self._experiment()
        self.app.sandbox_storage.upsert(
            experiment_id=exp_id,
            sandbox_uid="uid_beta_noid",
            project_id=self.project_id,
            provider="beta",  # owner recorded, id never was
            status="cleanup_pending",
            phase="cleanup_attempt_1",
        )
        self.app.sandbox_storage.upsert(
            experiment_id=exp_id,
            sandbox_uid="uid_alpha_sibling",
            project_id=self.project_id,
            sandbox_id="alpha:sb-alpha-sibling",
            provider="alpha",
            status="cleanup_pending",
            phase="cleanup_attempt_1",
        )
        self.alpha.by_experiment[exp_id] = "sb-alpha-sibling"
        self.alpha.alive["sb-alpha-sibling"] = True
        beta_lookups: list[str] = []
        inner = self.beta.find_sandbox_id

        def beta_find(*, experiment_id: str, sandbox_uid: str = "", **_kw):
            beta_lookups.append(sandbox_uid)
            return inner(experiment_id=experiment_id, sandbox_uid=sandbox_uid)

        self.beta.find_sandbox_id = beta_find  # type: ignore[method-assign]

        self.app.sandbox_lifecycle.terminate_vm(row=self._row("uid_beta_noid"))

        # alpha's attempt is untouched: still alive, never terminated.
        self.assertEqual(self.alpha.terminated, [])
        self.assertTrue(self.alpha.alive["sb-alpha-sibling"])
        self.assertEqual(self._row("uid_alpha_sibling")["status"], "cleanup_pending")
        # ...and the broad experiment-name sweep never ran while a sibling that
        # may still exist — parked counts — was attached to the experiment.
        self.assertEqual(beta_lookups, ["uid_beta_noid"])

    def test_a_parked_sibling_counts_as_one_that_may_still_exist(self) -> None:
        # The guard underneath the test above, in its own right.
        exp_id = self._experiment()
        self.app.sandbox_storage.upsert(
            experiment_id=exp_id,
            sandbox_uid="uid_parked_sibling",
            project_id=self.project_id,
            sandbox_id="alpha:sb-parked",
            provider="alpha",
            status="cleanup_pending",
        )

        self.assertTrue(
            self.app.sandbox_storage.has_active_for_experiment(
                experiment_id=exp_id, exclude_sandbox_uid="uid_other"
            )
        )

    # ---- a recorded owner nobody can reach ----

    def test_an_unconfigured_owner_is_unavailable_never_a_false_dead(self) -> None:
        # gamma was dropped from MERV_EXECUTION_BACKENDS. No configured provider
        # can answer for its ids, and "nobody here has it" is not evidence.
        exp_id = self._legacy_row(provider="gamma", sandbox_id="sb-gamma")
        lifecycle = self.app.sandbox_lifecycle

        self.assertIsNone(lifecycle.liveness(row=self._row()))

        view = self.app.sandboxes.get(project_id=self.project_id, experiment_id=exp_id)

        self.assertEqual(view["status"], "running")  # parked, not terminalized
        self.assertEqual(self._row()["status"], "running")
        self.assertEqual(self.alpha.terminated, [])
        self.assertEqual(self.beta.terminated, [])


class BuildFactoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_single_name_env_builds_direct_backend(self) -> None:
        with mock.patch.dict(
            os.environ, {"MERV_EXECUTION_BACKENDS": "fake"}, clear=False
        ):
            backend = build_sandbox_backend(repo_root=self.repo)

        self.assertIsInstance(backend, FakeSandboxBackend)

    def test_multiple_names_build_multiplexer_with_legacy_default(self) -> None:
        env = {
            "MERV_EXECUTION_BACKENDS": "fake, lambda_labs",
            "MERV_EXECUTION_BACKEND": "lambda_labs",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            backend = build_sandbox_backend(repo_root=self.repo)

        self.assertIsInstance(backend, MultiplexingSandboxBackend)
        self.assertEqual(backend.default, "lambda_labs")
        self.assertEqual(sorted(backend.backends), ["fake", "lambda_labs"])

    def test_multiple_names_default_to_first_configured(self) -> None:
        env = {"MERV_EXECUTION_BACKENDS": "fake,lambda_labs"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("MERV_EXECUTION_BACKEND", None)
            backend = build_sandbox_backend(repo_root=self.repo)

        self.assertIsInstance(backend, MultiplexingSandboxBackend)
        self.assertEqual(backend.default, "fake")

    def test_explicit_name_arg_bypasses_multi_config(self) -> None:
        env = {"MERV_EXECUTION_BACKENDS": "fake,lambda_labs"}
        with mock.patch.dict(os.environ, env, clear=False):
            backend = build_sandbox_backend(repo_root=self.repo, name="fake")

        self.assertIsInstance(backend, FakeSandboxBackend)


if __name__ == "__main__":
    unittest.main()
