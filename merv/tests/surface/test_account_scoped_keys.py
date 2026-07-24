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


if __name__ == "__main__":
    unittest.main()
