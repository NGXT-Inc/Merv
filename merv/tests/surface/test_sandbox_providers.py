"""Provider connections behind Sandboxes → Configure.

Covers the write-only credential contract (secrets never echo, non-secret
values re-render), partial-update semantics at the store, the request-time
disable gate wired into SandboxEngine, and the human-session boundary on the
HTTP writes.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import jwt
from fastapi.testclient import TestClient

from tests.support.brain import TestBrain
from tests.support.sandbox_backend import FakeSandboxBackend
from merv.brain.kernel.utils import PermissionDeniedError, ValidationError
from merv.brain.surface.auth import SupabaseVerifier
from merv.brain.surface.transport.api import create_fastapi_app
from merv.brain.surface.transport.http_policy import HttpSurfacePolicy

SECRET = "sandbox-provider-tests-jwt-secret-32b"
USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _token(user_id: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
            "session_id": f"session-{user_id[:4]}",
        },
        SECRET,
        algorithm="HS256",
    )


def _bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


class SandboxProviderSettingsTest(unittest.TestCase):
    """Service + store semantics on a local brain (no HTTP auth in play)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.brain = TestBrain(
            repo_root=root,
            db_path=root / "state.sqlite",
            execution_backend=FakeSandboxBackend(),
        )
        self.surface = self.brain.server.app
        self.settings = self.surface.sandbox_providers
        self.project_id = str(
            self.surface.research.create_project(name="Providers")["id"]
        )

    def tearDown(self) -> None:
        self.brain.shutdown()
        self.tmp.cleanup()

    def test_overview_lists_connectable_providers_without_modal(self) -> None:
        overview = self.settings.overview(project_id=self.project_id)
        names = [entry["provider"] for entry in overview["providers"]]
        self.assertIn("aws", names)
        self.assertIn("gcp", names)
        self.assertIn("azure", names)
        self.assertNotIn("modal", names)
        self.assertEqual(len(names), 10)
        # No rows yet: everything enabled by default, nothing connected.
        self.assertTrue(all(e["enabled"] for e in overview["providers"]))
        self.assertTrue(all(not e["connected"] for e in overview["providers"]))

    def test_credentials_merge_clear_and_never_echo_secrets(self) -> None:
        entry = self.settings.set_credentials(
            project_id=self.project_id,
            provider="aws",
            values={
                "MERV_AWS_ACCESS_KEY_ID": "AKIAEXAMPLE",
                "MERV_AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI",
            },
        )
        self.assertTrue(entry["connected"])
        self.assertEqual(entry["credential_source"], "saved")
        by_key = {f["key"]: f for f in entry["fields"]}
        # The secret reports set-ness only; the non-secret value re-renders.
        self.assertTrue(by_key["MERV_AWS_SECRET_ACCESS_KEY"]["set"])
        self.assertEqual(by_key["MERV_AWS_SECRET_ACCESS_KEY"]["value"], "")
        self.assertEqual(by_key["MERV_AWS_ACCESS_KEY_ID"]["value"], "AKIAEXAMPLE")
        self.assertNotIn("wJalrXUtnFEMI", json.dumps(entry))

        # Partial update keeps the untouched field; empty string clears.
        entry = self.settings.set_credentials(
            project_id=self.project_id,
            provider="aws",
            values={"MERV_AWS_REGION": "us-west-2"},
        )
        by_key = {f["key"]: f for f in entry["fields"]}
        self.assertTrue(by_key["MERV_AWS_SECRET_ACCESS_KEY"]["set"])
        self.assertEqual(by_key["MERV_AWS_REGION"]["value"], "us-west-2")
        entry = self.settings.set_credentials(
            project_id=self.project_id,
            provider="aws",
            values={"MERV_AWS_SECRET_ACCESS_KEY": ""},
        )
        by_key = {f["key"]: f for f in entry["fields"]}
        self.assertFalse(by_key["MERV_AWS_SECRET_ACCESS_KEY"]["set"])
        self.assertFalse(entry["connected"])

        # The internal read (provisioning's path) sees the stored values.
        saved = json.loads(
            self.brain.store.sandbox_provider_credentials(
                project_id=self.project_id, provider="aws"
            )
        )
        self.assertEqual(saved.get("MERV_AWS_REGION"), "us-west-2")
        self.assertNotIn("MERV_AWS_SECRET_ACCESS_KEY", saved)

    def test_unknown_provider_and_field_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            self.settings.set_credentials(
                project_id=self.project_id, provider="modal", values={"X": "y"}
            )
        with self.assertRaises(ValidationError):
            self.settings.set_credentials(
                project_id=self.project_id,
                provider="aws",
                values={"MERV_GCP_PROJECT": "nope"},
            )
        with self.assertRaises(ValidationError):
            self.settings.set_credentials(
                project_id=self.project_id, provider="aws", values={}
            )

    def test_cap_first_then_connect_still_starts_disabled(self) -> None:
        # A daily-limit row set before the wizard ran must not smuggle the
        # provider into the enabled set when credentials arrive later.
        self.settings.set_daily_limit(
            project_id=self.project_id, provider="tensordock", daily_usd_limit=25
        )
        entry = self.settings.set_credentials(
            project_id=self.project_id,
            provider="tensordock",
            values={"MERV_TENSORDOCK_TOKEN": "td_first"},
        )
        self.assertFalse(entry["enabled"])
        self.assertEqual(entry["daily_usd_limit"], 25)

    def test_mode_change_resets_verification(self) -> None:
        from merv.brain.sandbox.adapters import (
            CONNECTABLE_PROVIDERS,
            configured_backend_names,
        )
        from merv.brain.surface.sandbox_providers import SandboxProviderSettings

        service = SandboxProviderSettings(
            store=self.brain.store,
            fleet=configured_backend_names,
            catalog=CONNECTABLE_PROVIDERS,
            checks={"lambda_labs": lambda values: "ok"},
        )
        with patch.dict(os.environ, {"MERV_LAMBDA_API_KEY": "ll_platform"}):
            service.set_credentials(
                project_id=self.project_id,
                provider="lambda_labs",
                values=None,
                mode="platform",
            )
            result = service.verify(
                project_id=self.project_id, provider="lambda_labs"
            )
            self.assertTrue(result["ok"])
            # Switching to own credentials changes what a provision would
            # use, so the platform-mode verification stamp must not survive.
            entry = service.set_credentials(
                project_id=self.project_id,
                provider="lambda_labs",
                values=None,
                mode="own",
            )
            self.assertEqual(entry["verified_at"], "")

    def test_toggle_keeps_credentials_and_credentials_keep_toggle(self) -> None:
        self.settings.set_credentials(
            project_id=self.project_id,
            provider="tensordock",
            values={"MERV_TENSORDOCK_TOKEN": "td_secret"},
        )
        entry = self.settings.set_enabled(
            project_id=self.project_id, provider="tensordock", enabled=False
        )
        self.assertFalse(entry["enabled"])
        self.assertTrue(entry["connected"])  # toggle did not wipe credentials
        entry = self.settings.set_credentials(
            project_id=self.project_id,
            provider="tensordock",
            values={"MERV_TENSORDOCK_TOKEN": "td_secret_2"},
        )
        self.assertFalse(entry["enabled"])  # credential save kept the toggle

    def test_env_configuration_is_reported(self) -> None:
        with patch.dict(os.environ, {"LAMBDA_API_KEY": "ll_from_env"}):
            overview = self.settings.overview(project_id=self.project_id)
        lam = next(
            e for e in overview["providers"] if e["provider"] == "lambda_labs"
        )
        self.assertTrue(lam["env_configured"])
        self.assertEqual(lam["credential_source"], "env")
        self.assertFalse(lam["connected"])  # env-configured, nothing saved

    def test_disabled_provider_blocks_sandbox_request(self) -> None:
        # The fake backend's provider name is "fake"; a disable row for it
        # must stop procurement at admission, before any provisioning.
        self.brain.store.upsert_sandbox_provider_settings(
            project_id=self.project_id, provider="fake", enabled=False
        )
        with self.assertRaises(ValidationError) as ctx:
            self.surface.sandboxes.request(project_id=self.project_id)
        self.assertIn("disabled", str(ctx.exception))

    def test_enabled_row_admits_the_provider(self) -> None:
        self.brain.store.upsert_sandbox_provider_settings(
            project_id=self.project_id, provider="fake", enabled=True
        )
        # Admission passes silently; no-row providers pass too.
        self.settings.ensure_provider_allowed(
            project_id=self.project_id, provider="fake"
        )
        self.settings.ensure_provider_allowed(
            project_id=self.project_id, provider="aws"
        )

    def test_enable_requires_a_completed_setup(self) -> None:
        with self.assertRaises(ValidationError) as ctx:
            self.settings.set_enabled(
                project_id=self.project_id, provider="voltage_park", enabled=True
            )
        self.assertIn("not set up", str(ctx.exception))
        # Disabling an un-set-up provider is always allowed.
        entry = self.settings.set_enabled(
            project_id=self.project_id, provider="voltage_park", enabled=False
        )
        self.assertFalse(entry["enabled"])
        entry = self.settings.set_credentials(
            project_id=self.project_id,
            provider="voltage_park",
            values={"MERV_VOLTAGE_PARK_TOKEN": "vp_secret"},
        )
        # Connecting never enables as a side effect — that is the explicit act.
        self.assertFalse(entry["enabled"])
        entry = self.settings.set_enabled(
            project_id=self.project_id, provider="voltage_park", enabled=True
        )
        self.assertTrue(entry["enabled"])
        self.assertTrue(entry["setup_complete"])

    def test_platform_credentials_flow_for_lambda(self) -> None:
        # Without deployment env creds the platform choice is absent and
        # selecting it is refused.
        overview = self.settings.overview(project_id=self.project_id)
        lam = next(
            e for e in overview["providers"] if e["provider"] == "lambda_labs"
        )
        self.assertFalse(lam["platform_available"])
        with self.assertRaises(ValidationError):
            self.settings.set_credentials(
                project_id=self.project_id,
                provider="lambda_labs",
                values=None,
                mode="platform",
            )
        with patch.dict(os.environ, {"MERV_LAMBDA_API_KEY": "ll_platform"}):
            entry = self.settings.set_credentials(
                project_id=self.project_id,
                provider="lambda_labs",
                values=None,
                mode="platform",
            )
            self.assertTrue(entry["platform_available"])
            self.assertEqual(entry["credential_mode"], "platform")
            self.assertEqual(entry["credential_source"], "platform")
            self.assertTrue(entry["setup_complete"])
            enabled = self.settings.set_enabled(
                project_id=self.project_id, provider="lambda_labs", enabled=True
            )
            self.assertTrue(enabled["enabled"])

    def test_verify_stamps_and_credential_writes_reset(self) -> None:
        from merv.brain.sandbox.adapters import (
            CONNECTABLE_PROVIDERS,
            configured_backend_names,
        )
        from merv.brain.surface.sandbox_providers import SandboxProviderSettings

        seen: dict[str, dict[str, str]] = {}

        def good(values):  # noqa: ANN001
            seen["values"] = dict(values)
            return "account ok"

        def bad(values):  # noqa: ANN001
            raise ValidationError("key rejected upstream")

        service = SandboxProviderSettings(
            store=self.brain.store,
            fleet=configured_backend_names,
            catalog=CONNECTABLE_PROVIDERS,
            checks={"tensordock": good, "voltage_park": bad},
        )
        service.set_credentials(
            project_id=self.project_id,
            provider="tensordock",
            values={"MERV_TENSORDOCK_TOKEN": "td_secret"},
        )
        result = service.verify(
            project_id=self.project_id, provider="tensordock"
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["detail"], "account ok")
        self.assertTrue(result["provider"]["verified_at"])
        # The check received the effective (saved) values.
        self.assertEqual(seen["values"]["MERV_TENSORDOCK_TOKEN"], "td_secret")
        # A credential rewrite invalidates the stamp.
        entry = service.set_credentials(
            project_id=self.project_id,
            provider="tensordock",
            values={"MERV_TENSORDOCK_TOKEN": "td_secret_2"},
        )
        self.assertEqual(entry["verified_at"], "")
        # A failing check reports the reason and stamps nothing.
        service.set_credentials(
            project_id=self.project_id,
            provider="voltage_park",
            values={"MERV_VOLTAGE_PARK_TOKEN": "vp_x"},
        )
        result = service.verify(
            project_id=self.project_id, provider="voltage_park"
        )
        self.assertFalse(result["ok"])
        self.assertIn("rejected", result["detail"])
        self.assertEqual(result["provider"]["verified_at"], "")

    def test_daily_limit_blocks_new_provisioning(self) -> None:
        entry = self.settings.set_daily_limit(
            project_id=self.project_id, provider="aws", daily_usd_limit=12.5
        )
        self.assertEqual(entry["daily_usd_limit"], 12.5)
        with self.assertRaises(ValidationError):
            self.settings.set_daily_limit(
                project_id=self.project_id, provider="aws", daily_usd_limit=-1
            )
        # A zero cap on the fake provider blocks the next request outright.
        self.brain.store.set_sandbox_provider_daily_limit(
            project_id=self.project_id, provider="fake", daily_usd_limit=0.0
        )
        with self.assertRaises(PermissionDeniedError) as ctx:
            self.surface.sandboxes.request(
                project_id=self.project_id,
                public_key="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITESTKEY dev@test",
            )
        self.assertIn("daily spend limit", str(ctx.exception))
        # Clearing the cap lifts the block at admission ("fake" is a test
        # backend, not a catalog provider, so clear it at the store).
        self.brain.store.set_sandbox_provider_daily_limit(
            project_id=self.project_id, provider="fake", daily_usd_limit=None
        )
        rows = self.brain.store.list_sandbox_provider_settings(
            project_id=self.project_id
        )
        fake = next(r for r in rows if r["provider"] == "fake")
        self.assertIsNone(fake["daily_usd_limit"])

    def test_provider_day_spend_clamps_to_today(self) -> None:
        from datetime import UTC, datetime, timedelta

        from merv.brain.sandbox.quotas import QuotaService

        now = datetime.now(UTC)
        with self.brain.store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sandbox_generations
                  (id, experiment_id, project_id, tenant_id, provider,
                   price_usd_per_hour, started_at, ended_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "gen_today",
                    "exp_x",
                    self.project_id,
                    "local",
                    "fake",
                    2.0,
                    (now - timedelta(hours=2)).isoformat(),
                    (now - timedelta(hours=1)).isoformat(),
                ),
            )
        # Clamp-aware expectation so the test is stable across UTC midnight.
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        started = max(now - timedelta(hours=2), day_start)
        ended = now - timedelta(hours=1)
        expected = max(0.0, (ended - started).total_seconds() / 3600.0) * 2.0
        spend = QuotaService(store=self.brain.store).provider_day_spend(
            project_id=self.project_id, provider="fake"
        )
        self.assertAlmostEqual(spend, expected, places=2)
        # Another provider's ledger is untouched by this one's cap math.
        other = QuotaService(store=self.brain.store).provider_day_spend(
            project_id=self.project_id, provider="aws"
        )
        self.assertEqual(other, 0.0)


def _postgrest(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=[])


class SandboxProviderHttpBoundaryTest(unittest.TestCase):
    """Machine keys may read the overview; only humans may rewire clouds."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.brain = TestBrain(
            repo_root=root,
            db_path=root / "state.sqlite",
            execution_backend=FakeSandboxBackend(),
        )
        from merv.brain.surface.project_keys import ProjectKeys

        self.keys = ProjectKeys(store=self.brain.store)
        self.verifier = SupabaseVerifier(
            supabase_url="https://example.supabase.co",
            jwt_secret=SECRET,
            service_key="service-key",
            project_keys=self.keys,
        )
        self.verifier._http = httpx.Client(transport=httpx.MockTransport(_postgrest))
        self.client = TestClient(
            create_fastapi_app(
                self.brain,
                surface_policy=HttpSurfacePolicy.for_surface(
                    restrict_cors=True, hosted_control=True
                ),
                auth=self.verifier,
            ),
            raise_server_exceptions=False,
        )
        self.jwt_a = _token(USER_A)
        created = self.client.post(
            "/api/projects", json={"name": "Clouds"}, headers=_bearer(self.jwt_a)
        )
        assert created.status_code == 201, created.text
        self.project_id = str(created.json()["id"])
        minted = self.client.post(
            f"/api/projects/{self.project_id}/keys",
            json={},
            headers=_bearer(self.jwt_a),
        )
        assert minted.status_code == 201, minted.text
        self.mk_key = minted.json()["secret"]

    def tearDown(self) -> None:
        self.verifier._http.close()
        self.brain.shutdown()
        self.tmp.cleanup()

    def test_browser_session_saves_and_toggles(self) -> None:
        saved = self.client.put(
            f"/api/projects/{self.project_id}/sandbox-providers/aws",
            json={"values": {"MERV_AWS_ACCESS_KEY_ID": "AKIA1"}},
            headers=_bearer(self.jwt_a),
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertFalse(saved.json()["connected"])  # secret still missing
        toggled = self.client.post(
            f"/api/projects/{self.project_id}/sandbox-providers/aws/enabled",
            json={"enabled": False},
            headers=_bearer(self.jwt_a),
        )
        self.assertEqual(toggled.status_code, 200, toggled.text)
        self.assertFalse(toggled.json()["enabled"])
        overview = self.client.get(
            f"/api/projects/{self.project_id}/sandbox-providers",
            headers=_bearer(self.jwt_a),
        )
        self.assertEqual(overview.status_code, 200, overview.text)
        aws = next(
            e for e in overview.json()["providers"] if e["provider"] == "aws"
        )
        self.assertFalse(aws["enabled"])

    def test_machine_key_reads_but_cannot_write(self) -> None:
        overview = self.client.get(
            f"/api/projects/{self.project_id}/sandbox-providers",
            headers=_bearer(self.mk_key),
        )
        self.assertEqual(overview.status_code, 200, overview.text)
        denied_save = self.client.put(
            f"/api/projects/{self.project_id}/sandbox-providers/aws",
            json={"values": {"MERV_AWS_ACCESS_KEY_ID": "AKIA1"}},
            headers=_bearer(self.mk_key),
        )
        self.assertEqual(denied_save.status_code, 403, denied_save.text)
        denied_toggle = self.client.post(
            f"/api/projects/{self.project_id}/sandbox-providers/aws/enabled",
            json={"enabled": False},
            headers=_bearer(self.mk_key),
        )
        self.assertEqual(denied_toggle.status_code, 403, denied_toggle.text)


if __name__ == "__main__":
    unittest.main()
