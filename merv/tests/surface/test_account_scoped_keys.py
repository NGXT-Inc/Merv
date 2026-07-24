"""Enforcement boundaries keyed on credential SHAPE, not project binding.

An account-scoped (``mk_``) key carries ``key_id`` but no ``key_project_id``.
Every deny-rule that used to test the binding would fail open for such a key,
so each rule now tests ``is_external_key``. These cases construct the principal
directly: the mint path for unbound keys arrives with the ``grant_scope``
column, and these boundaries must already hold before it does.
"""

from __future__ import annotations

import unittest
from typing import Any

from starlette.requests import Request

from merv.brain.surface.identity import (
    LOCAL_PRINCIPAL,
    Principal,
    ProjectKeyScopeError,
)
from merv.brain.surface.transport.api.gateway import ProjectAuthorizer
from merv.brain.surface.transport.api.mcp_preauth import build_mcp_preauthorizer

PROJECT_A = "proj-a"
USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

# A key bound to one project: key_id AND key_project_id.
BOUND_KEY = Principal(
    tenant_id="local", client_id="project-key:k1", user_id=USER_A,
    key_id="k1", key_project_id=PROJECT_A,
)
# An account-scoped key: key_id, but NO project binding. The shape that made
# every `if key_project_id and ...` rule fail open.
ACCOUNT_KEY = Principal(
    tenant_id="local", client_id="project-key:k2", user_id=USER_A, key_id="k2",
)
# rr_sk_ and JWT carry no key_id at all; their reach is deliberately unchanged.
RR_KEY = Principal(tenant_id="local", client_id="key:abcd1234", user_id=USER_A)
JWT = Principal(tenant_id="local", client_id="jwt:session", user_id=USER_A)


class _Projects:
    """Membership stub: every deny-rule under test short-circuits before this."""

    def is_member(self, *, project_id: str, user_id: str) -> bool:
        return True

    def request_project_id(self, *, review_request_id: Any) -> str:
        return ""

    def session_project_id(self, *, review_session_id: Any) -> str:
        return ""


def _request(path: str, principal: Principal, query: str = "") -> Request:
    request = Request(
        {
            "type": "http", "method": "GET", "path": path,
            "query_string": query.encode(), "headers": [],
        }
    )
    request.state.principal = principal
    return request


class OperatorDiagnosticsShapeTest(unittest.TestCase):
    """INV-11: no external key reaches operator diagnostics, bound or not."""

    def setUp(self) -> None:
        self.authorizer = ProjectAuthorizer(projects=_Projects())

    def _denial(self, path: str, principal: Principal, query: str = ""):
        return self.authorizer.http_denial(_request(path, principal, query))

    def test_every_external_key_shape_is_denied_operator_diagnostics(self) -> None:
        for path in ("/api/activity", "/api/debug/state", "/api/admin/keys"):
            for label, principal in (("bound", BOUND_KEY), ("account", ACCOUNT_KEY)):
                with self.subTest(path=path, key=label):
                    denial = self._denial(path, principal)
                    self.assertIsNotNone(
                        denial, f"{label} key reached operator diagnostics at {path}"
                    )
                    self.assertEqual(denial.status_code, 403)

    def test_non_key_credentials_keep_their_existing_reach(self) -> None:
        # rr_sk_ stays an owner-trust credential (ruled: leave as-is), so this
        # change must not narrow it. /api/activity is membership-scoped, hence
        # the explicit project_id.
        for label, principal in (("rr_sk_", RR_KEY), ("jwt", JWT)):
            with self.subTest(credential=label):
                denial = self._denial(
                    "/api/activity", principal, query=f"project_id={PROJECT_A}"
                )
                self.assertIsNone(denial)


class ProjectCreateShapeTest(unittest.TestCase):
    """An account-scoped key is still a machine credential: no project.create."""

    def setUp(self) -> None:
        projects = _Projects()
        self.preauthorize = build_mcp_preauthorizer(
            authorizer=ProjectAuthorizer(projects=projects),
            reviews=projects,
            hosted=True,
        )

    def _create(self, principal: Principal) -> None:
        self.preauthorize(
            _request("/mcp", principal),
            "project",
            {"action": "create", "name": "New Project"},
        )

    def test_every_external_key_shape_is_barred_from_project_create(self) -> None:
        for label, principal in (("bound", BOUND_KEY), ("account", ACCOUNT_KEY)):
            with self.subTest(key=label):
                with self.assertRaises(ProjectKeyScopeError):
                    self._create(principal)

    def test_human_and_local_credentials_may_still_create(self) -> None:
        for label, principal in (
            ("jwt", JWT), ("rr_sk_", RR_KEY), ("local", LOCAL_PRINCIPAL),
        ):
            with self.subTest(credential=label):
                self._create(principal)  # no raise


class AccountKeyOverTheWireTest(unittest.TestCase):
    """A real account-scoped key against the real surface.

    Everything here was unconstructible before the scope column existed, which
    is why the fail-open it guards against could not be caught by any test.
    """

    def setUp(self) -> None:
        from tests.surface.test_project_keys import (
            SECRET, USER_A, _bearer, _postgrest, _token,
        )

        import tempfile
        from pathlib import Path

        import httpx
        from fastapi.testclient import TestClient

        from merv.brain.sandbox.execution.backends.fake import FakeSandboxBackend
        from merv.brain.surface.auth import SupabaseVerifier
        from merv.brain.surface.project_key_store import SqlProjectKeyRepository
        from merv.brain.surface.project_keys import ProjectKeys
        from merv.brain.surface.transport.http_api import create_fastapi_app
        from merv.brain.surface.transport.http_policy import HttpSurfacePolicy
        from tests.support.brain import TestBrain

        self._bearer = _bearer
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = TestBrain(
            repo_root=root,
            db_path=root / "state.sqlite",
            execution_backend=FakeSandboxBackend(),
        )
        self.keys = ProjectKeys(repository=SqlProjectKeyRepository(store=self.app.store))
        self.verifier = SupabaseVerifier(
            supabase_url="https://example.supabase.co", jwt_secret=SECRET,
            service_key="service-key", project_keys=self.keys,
        )
        self.verifier._http = httpx.Client(transport=httpx.MockTransport(_postgrest))
        self.client = TestClient(
            create_fastapi_app(
                self.app.http,
                surface_policy=HttpSurfacePolicy.for_surface(
                    restrict_cors=True, hosted_control=True
                ),
                auth=self.verifier,
            ),
            raise_server_exceptions=False,
        )
        self.jwt = _token(USER_A)
        self.project_a = self._project("Account Project A")
        self.project_b = self._project("Account Project B")
        # Home project is A; the grant reaches B all the same.
        self.key = str(
            self.client.post(
                f"/api/projects/{self.project_a}/keys",
                json={"grant_scope": "account"},
                headers=_bearer(self.jwt),
            ).json()["secret"]
        )

    def tearDown(self) -> None:
        self.verifier._http.close()
        self.app.shutdown()
        self.tmp.cleanup()

    def _project(self, name: str) -> str:
        response = self.client.post(
            "/api/projects", json={"name": name}, headers=self._bearer(self.jwt)
        )
        self.assertEqual(response.status_code, 201, response.text)
        return str(response.json()["id"])

    def test_the_principal_carries_no_project_confinement(self) -> None:
        principal = self.verifier.verify_bearer(f"Bearer {self.key}")
        self.assertIsNotNone(principal.key_id)  # still an external key
        self.assertIsNone(principal.key_project_id)  # but confined to nothing

    def test_it_reaches_every_project_its_owner_belongs_to(self) -> None:
        for label, project_id in (("home", self.project_a), ("other", self.project_b)):
            with self.subTest(project=label):
                response = self.client.get(
                    f"/api/projects/{project_id}", headers=self._bearer(self.key)
                )
                self.assertEqual(response.status_code, 200, response.text)

    def test_it_stops_at_the_edge_of_membership(self) -> None:
        # A project belonging to somebody else stays invisible: the account
        # grant widens reach to the owner's membership, never past it.
        outsider = self.app.projects.create(
            name="Someone Else", user_id=USER_B_ID
        )["id"]
        response = self.client.get(
            f"/api/projects/{outsider}", headers=self._bearer(self.key)
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_it_is_still_barred_from_operator_diagnostics(self) -> None:
        # The phase-1 fail-open, now exercised by a real credential.
        for path in ("/api/activity", "/api/admin/cleanup"):
            with self.subTest(path=path):
                response = self.client.post(path, headers=self._bearer(self.key))
                if response.status_code == 405:
                    response = self.client.get(path, headers=self._bearer(self.key))
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(
                    response.json()["error_code"], "project_scope_forbidden"
                )

    def test_it_is_still_barred_from_creating_projects(self) -> None:
        response = self.client.post(
            "/mcp/call",
            json={"name": "project", "arguments": {"action": "create", "name": "X"}},
            headers=self._bearer(self.key),
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["error_code"], "project_scope_forbidden")


class GrantScopeMigrationTest(unittest.TestCase):
    """Migration 34 upgrades a pre-existing database in place."""

    def test_existing_rows_become_project_scoped_and_new_rows_may_differ(self) -> None:
        import sqlite3
        import tempfile
        from pathlib import Path

        from merv.brain.kernel.state.store import GRANT_SCOPE_TABLES, StateStore

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.sqlite"
            StateStore(db_path=path).connect().close()
            # Simulate a database from before 34 by dropping the column back
            # off every credential table, then re-running migrations.
            with sqlite3.connect(path) as conn:
                for table in GRANT_SCOPE_TABLES:
                    conn.execute(f"ALTER TABLE {table} DROP COLUMN grant_scope")
                conn.execute("DELETE FROM schema_migrations WHERE version = 34")
                conn.commit()

            StateStore(db_path=path).connect().close()

            with sqlite3.connect(path) as conn:
                conn.row_factory = sqlite3.Row
                for table in GRANT_SCOPE_TABLES:
                    info = {
                        row["name"]: row
                        for row in conn.execute(f"PRAGMA table_info({table})")
                    }
                    self.assertIn("grant_scope", info, table)
                    self.assertEqual(info["grant_scope"]["notnull"], 1, table)
                    self.assertEqual(
                        info["grant_scope"]["dflt_value"], "'project'", table
                    )
                applied = conn.execute(
                    "SELECT name FROM schema_migrations WHERE version = 34"
                ).fetchone()
                self.assertEqual(applied["name"], "add_grant_scope")
                # The CHECK survives ALTER TABLE ADD COLUMN, so a migrated
                # database cannot drift to a scope the enforcement layer does
                # not understand. (Raw sqlite3 leaves foreign_keys OFF, so the
                # asserted message confirms it is the CHECK and not the FK.)
                conn.execute(
                    "INSERT INTO oauth_clients (client_id, client_name, "
                    "redirect_uris_json, grant_types_json, created_at) "
                    "VALUES ('c1', 'n', '[]', '[]', 'now')"
                )
                with self.assertRaises(sqlite3.IntegrityError) as caught:
                    conn.execute(
                        "INSERT INTO oauth_authorization_codes (code_digest, "
                        "client_id, redirect_uri, owner_user_id, project_id, "
                        "grant_scope, code_challenge, resource, created_at, "
                        "expires_at) VALUES ('d', 'c1', 'u', 'o', 'p', "
                        "'everything', 'ch', 'r', 'now', 'later')"
                    )
                self.assertIn("CHECK constraint failed", str(caught.exception))
                self.assertIn("grant_scope", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
