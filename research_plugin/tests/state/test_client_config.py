from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from backend.client_cli import (
    ClientError,
    _daemon_endpoint,
    _daemon_ready,
    configure_client,
    main,
    stop_daemon,
)
from backend.config import (
    CLIENT_CONFIG_ENV_VAR,
    CONTROL_URL_ENV_VAR,
    read_client_config,
    resolve_control_url,
    resolve_daemon_state_dir,
)
from backend.dataplane.project_links import ProjectLinks
from mcp_server.__main__ import _repo_is_linked


class ClientConfigTest(unittest.TestCase):
    def test_configure_writes_machine_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "client.json"
            config = configure_client(
                config_path=config_path,
                control_url="https://control.example.test/",
                daemon_url="http://127.0.0.1:8787",
            )

            self.assertEqual(config["control_url"], "https://control.example.test")
            self.assertTrue(config_path.exists())
            self.assertEqual(
                read_client_config({CLIENT_CONFIG_ENV_VAR: str(config_path)})["control_url"],
                "https://control.example.test",
            )
            self.assertEqual(
                resolve_daemon_state_dir({CLIENT_CONFIG_ENV_VAR: str(config_path)}).resolve(),
                config_path.parent.resolve(),
            )
            self.assertEqual(
                resolve_control_url({CLIENT_CONFIG_ENV_VAR: str(config_path)}),
                "https://control.example.test",
            )

    def test_explicit_env_overrides_machine_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "client.json"
            configured = configure_client(
                config_path=config_path,
                control_url="https://configured.example.test",
                daemon_url="http://127.0.0.1:8787",
            )

            env = {
                CLIENT_CONFIG_ENV_VAR: str(config_path),
                CONTROL_URL_ENV_VAR: "https://override.example.test",
            }
            self.assertEqual(resolve_control_url(env), "https://override.example.test")
            self.assertEqual(configured["control_url"], "https://configured.example.test")

    def test_daemon_endpoint_uses_config_unless_overridden(self) -> None:
        config = {"daemon_url": "http://127.0.0.1:18787"}

        self.assertEqual(
            _daemon_endpoint(
                config=config,
                args=argparse.Namespace(host=None, port=None),
            ),
            ("127.0.0.1", 18787),
        )
        with self.assertRaises(ClientError):
            _daemon_endpoint(
                config=config,
                args=argparse.Namespace(host="0.0.0.0", port=None),
            )
        self.assertEqual(
            _daemon_endpoint(
                config=config,
                args=argparse.Namespace(host=None, port=19000),
            ),
            ("127.0.0.1", 19000),
        )

    def test_daemon_ready_requires_cloud_reachability(self) -> None:
        self.assertTrue(_daemon_ready({"ok": True, "cloud_reachable": True}))
        self.assertFalse(_daemon_ready({"ok": True, "cloud_reachable": False}))
        self.assertFalse(_daemon_ready({"ok": False, "cloud_reachable": True}))

    def test_connect_configures_starts_and_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "client.json"
            repo = Path(tmp) / "repo"
            repo.mkdir()
            with (
                patch("backend.client_cli._cmd_start", return_value=0) as start,
                patch("backend.client_cli.link_repo", return_value={"linked": True}) as link,
            ):
                with redirect_stdout(io.StringIO()):
                    code = main(
                        [
                            "--config",
                            str(config_path),
                            "connect",
                            "--control-url",
                            "https://control.example.test",
                            "--project-id",
                            "proj_123",
                            "--repo",
                            str(repo),
                        ]
                    )

            self.assertEqual(code, 0)
            self.assertTrue(config_path.exists())
            start.assert_called_once()
            link.assert_called_once()
            self.assertEqual(link.call_args.kwargs["project_id"], "proj_123")
            self.assertEqual(link.call_args.kwargs["repo_root"], repo.resolve())

    def test_mcp_hosted_config_is_scoped_to_linked_repos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            linked = root / "linked"
            unlinked = root / "unlinked"
            linked.mkdir()
            unlinked.mkdir()
            links = ProjectLinks(db_path=root / "project_links.sqlite")
            links.link(repo_root=str(linked), project_id="proj_123")

            config = {"daemon_state_dir": str(root)}
            self.assertTrue(_repo_is_linked(config=config, repo_root=linked))
            self.assertFalse(_repo_is_linked(config=config, repo_root=unlinked))

    def test_stop_ignores_pid_that_is_not_daemon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "client.json"
            pid_path = Path(tmp) / "daemon.pid"
            pid_path.write_text("12345", encoding="utf-8")
            with (
                patch("backend.client_cli._pid_alive", return_value=True),
                patch("backend.client_cli._pid_looks_like_daemon", return_value=False),
                patch("backend.client_cli.os.kill") as kill,
            ):
                self.assertFalse(stop_daemon(config_path=config_path))

            kill.assert_not_called()
            self.assertFalse(pid_path.exists())


if __name__ == "__main__":
    unittest.main()
