from __future__ import annotations

import unittest
from unittest.mock import Mock

from merv.brain.application.tool_commands import ControlToolOperations
from merv.brain.kernel.utils import ValidationError


class ControlToolOperationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.projects = Mock()
        self.claims = Mock()
        self.experiments = Mock()
        self.storage = Mock()
        self.operations = ControlToolOperations(
            projects=self.projects,
            claims=self.claims,
            experiments=self.experiments,
            storage=self.storage,
        )

    def test_experiment_list_preserves_the_slim_projection_and_order(self) -> None:
        projected = {"experiments": [{"id": "exp_2"}, {"id": "exp_1"}]}
        self.experiments.agent.return_value = projected

        result = self.operations.experiment_list(project_id="proj_1")

        self.assertIs(result, projected)
        self.experiments.agent.assert_called_once_with(project_id="proj_1")

    def test_project_create_forwards_only_the_historical_arguments(self) -> None:
        self.projects.create.return_value = {"id": "proj_1"}

        result = self.operations.project(
            action="create",
            project_id="ignored",
            name="Project",
            summary="Summary",
            tenant_id="tenant_1",
            user_id="user_1",
        )

        self.assertEqual(result, {"id": "proj_1"})
        self.projects.create.assert_called_once_with(
            name="Project",
            summary="Summary",
            tenant_id="tenant_1",
            user_id="user_1",
        )

    def test_project_overview_reuses_claim_and_slim_experiment_projections(self) -> None:
        self.projects.get.return_value = {
            "id": "proj_1",
            "name": "Project",
            "summary": "Summary",
            "extra": "hidden",
        }
        self.claims.list_claims.return_value = {"claims": [{"id": "claim_1"}]}
        self.experiments.agent.return_value = {
            "experiments": [{"id": "exp_1", "status": "planned"}]
        }

        result = self.operations.project(action="overview", project_id="proj_1")

        self.assertEqual(
            result,
            {
                "project": {
                    "id": "proj_1",
                    "name": "Project",
                    "summary": "Summary",
                },
                "claims": [{"id": "claim_1"}],
                "experiments": [{"id": "exp_1", "status": "planned"}],
            },
        )
        self.projects.get.assert_called_once_with(project_id="proj_1")
        self.claims.list_claims.assert_called_once_with(project_id="proj_1")

    def test_project_current_returns_the_keys_bound_project(self) -> None:
        # D7: a keyed cloud caller reaches the brain (no proxy folder link);
        # the gateway injects key_project_id and current resolves it.
        self.projects.get.return_value = {
            "id": "proj_bound",
            "name": "Bound",
            "summary": "S",
            "extra": "hidden",
        }

        result = self.operations.project(action="current", key_project_id="proj_bound")

        self.assertEqual(
            result,
            {
                "exists": True,
                "project": {"id": "proj_bound", "name": "Bound", "summary": "S"},
            },
        )
        self.projects.get.assert_called_once_with(project_id="proj_bound")

    def test_project_current_without_a_bound_project_lists_what_it_can_reach(
        self,
    ) -> None:
        self.projects.list_projects.return_value = {
            "projects": [
                {
                    "id": "proj_1", "name": "One", "summary": "First",
                    "status": "active", "created_at": "2026-07-01T00:00:00Z",
                },
                {
                    "id": "proj_2", "name": "Two", "summary": "Second",
                    "status": "active", "created_at": "2026-07-02T00:00:00Z",
                },
            ]
        }

        result = self.operations.project(action="current", user_id="user_1")

        self.assertFalse(result["exists"])
        # The old response told the agent to go mint a per-project key. An
        # unbound credential is now the normal case, so it gets the list.
        self.assertNotIn("Mint", result["hint"])
        self.assertEqual([p["id"] for p in result["projects"]], ["proj_1", "proj_2"])
        self.projects.get.assert_not_called()
        self.projects.list_projects.assert_called_once_with(
            user_id="user_1", project_id=""
        )

    def test_project_list_returns_names_dates_and_summaries(self) -> None:
        self.projects.list_projects.return_value = {
            "projects": [
                {
                    "id": "proj_1", "name": "One", "summary": "First",
                    "status": "active", "created_at": "2026-07-01T00:00:00Z",
                    "settings": {"require_verified_reviews": True},
                }
            ]
        }

        result = self.operations.project(action="list", user_id="user_1")

        self.assertEqual(
            result["projects"],
            [
                {
                    "id": "proj_1",
                    "name": "One",
                    "summary": "First",
                    "status": "active",
                    "created_at": "2026-07-01T00:00:00Z",
                }
            ],
        )
        # Policy knobs are not the agent's business.
        self.assertNotIn("settings", result["projects"][0])

    def test_project_list_from_a_bound_key_stays_narrowed_to_that_project(
        self,
    ) -> None:
        self.projects.list_projects.return_value = {"projects": []}

        self.operations.project(
            action="list", user_id="user_1", key_project_id="proj_bound"
        )

        self.projects.list_projects.assert_called_once_with(
            user_id="user_1", project_id="proj_bound"
        )

    def test_project_overview_without_any_project_fails_closed(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            self.operations.project(action="overview", user_id="user_1")

        # Names the fix rather than guessing which project was meant.
        self.assertIn('action="list"', str(caught.exception))
        self.projects.get.assert_not_called()

    def test_project_overview_defaults_to_the_bound_project(self) -> None:
        self.projects.get.return_value = {"id": "proj_bound", "name": "B", "summary": ""}
        self.claims.list_claims.return_value = {"claims": []}
        self.experiments.agent.return_value = {"experiments": []}

        result = self.operations.project(action="overview", key_project_id="proj_bound")

        self.assertEqual(result["project"]["id"], "proj_bound")
        self.projects.get.assert_called_once_with(project_id="proj_bound")
        self.claims.list_claims.assert_called_once_with(project_id="proj_bound")

    def test_storage_find_preserves_resolve_and_list_modes(self) -> None:
        self.storage.resolve.return_value = {"object": {"id": "so_1"}}
        resolved = self.operations.storage_find(
            project_id="proj_1",
            object_id="so_1",
            version=3,
            include_download=False,
        )
        self.assertEqual(resolved, {"object": {"id": "so_1"}})
        self.storage.resolve.assert_called_once_with(
            project_id="proj_1",
            object_id="so_1",
            name=None,
            version=3,
            include_download=False,
        )
        self.storage.list_objects.return_value = {"objects": []}

        listed = self.operations.storage_find(
            project_id="proj_1",
            kind="model",
            status="ready",
            include_expired=True,
            limit=5,
            offset=1,
            compact=True,
        )

        self.assertEqual(listed, {"objects": []})
        self.storage.list_objects.assert_called_once_with(
            project_id="proj_1",
            kind="model",
            status="ready",
            include_expired=True,
            limit=5,
            offset=1,
            compact=True,
        )

    def test_storage_object_routes_each_action_and_preserves_unknown_error(self) -> None:
        for action in ("pin", "unpin", "renew", "delete"):
            with self.subTest(action=action):
                operation = getattr(self.storage, action)
                operation.return_value = {"action": action}
                self.assertEqual(
                    self.operations.storage_object(
                        project_id="proj_1", object_id="so_1", action=action
                    ),
                    {"action": action},
                )
                operation.assert_called_once_with(
                    project_id="proj_1", object_id="so_1"
                )

        with self.assertRaisesRegex(
            ValidationError, "unknown storage object action: purge"
        ):
            self.operations.storage_object(
                project_id="proj_1", object_id="so_1", action="purge"
            )


if __name__ == "__main__":
    unittest.main()
