from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from backend.daemon.daemon_loopback import create_daemon_loopback_app
from backend.dataplane.project_links import ProjectLinks


def _client(links: ProjectLinks) -> TestClient:
    class _Control:
        @staticmethod
        def list_tools():
            return []

    class _Daemon:
        loopback_secret = "local-secret"
        project_links = links
        control = _Control()

    return TestClient(create_daemon_loopback_app(daemon=_Daemon()))


class ClientLinkingTest(unittest.TestCase):
    def test_daemon_links_many_folders_to_many_projects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo_a = root / "repo-a"
            repo_b = root / "repo-b"
            repo_a.mkdir()
            repo_b.mkdir()
            links = ProjectLinks(db_path=root / "links.sqlite")
            client = _client(links)
            headers = {"Authorization": "Bearer local-secret"}

            for repo, project_id in ((repo_a, "proj_a"), (repo_b, "proj_b")):
                response = client.post(
                    "/local/link",
                    headers=headers,
                    json={"repo_root": str(repo), "project_id": project_id},
                )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["linked"])

            route_a = client.get(
                "/local/route", headers=headers, params={"repo_root": str(repo_a)}
            )
            route_b = client.get(
                "/local/route", headers=headers, params={"repo_root": str(repo_b)}
            )
            self.assertEqual(route_a.json()["project_id"], "proj_a")
            self.assertEqual(route_b.json()["project_id"], "proj_b")

            listed = client.get("/local/links", headers=headers)
            self.assertEqual(listed.status_code, 200, listed.text)
            got = {
                row["repo_root"]: row["project_id"]
                for row in listed.json()["links"]
            }
            self.assertEqual(got[str(repo_a.resolve())], "proj_a")
            self.assertEqual(got[str(repo_b.resolve())], "proj_b")

            removed = client.delete(
                "/local/link", headers=headers, params={"repo_root": str(repo_a)}
            )
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertTrue(removed.json()["unlinked"])
            route_a_after = client.get(
                "/local/route", headers=headers, params={"repo_root": str(repo_a)}
            )
            route_b_after = client.get(
                "/local/route", headers=headers, params={"repo_root": str(repo_b)}
            )
            self.assertFalse(route_a_after.json()["exists"])
            self.assertEqual(route_b_after.json()["project_id"], "proj_b")

    def test_loopback_linking_requires_local_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            links = ProjectLinks(db_path=Path(tmp) / "links.sqlite")
            client = _client(links)
            response = client.get("/local/links")
            self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
