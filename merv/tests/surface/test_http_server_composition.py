"""The programmatic uvicorn wrapper is a composition root, and answers for it.

``make_http_server``/``UvicornHttpServer`` compose a FastAPI app themselves, so
they must reach the same hosted-auth decision every other composition does.
Omitting ``surface_policy`` selects the unauthenticated LOCAL default, which is
only honest on a loopback bind — hence the non-loopback refusal below.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from merv.brain.sandbox.execution.backends.fake import FakeSandboxBackend
from merv.brain.surface.transport.http_policy import HttpSurfacePolicy
from merv.brain.surface.transport.http_server import (
    is_loopback_host,
    make_http_server,
)
from merv.shared.errors import ValidationError
from tests.support.brain import TestBrain

HOSTED = HttpSurfacePolicy.for_surface(restrict_cors=True, hosted_control=True)


class HttpServerCompositionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = TestBrain(
            repo_root=root,
            db_path=root / "state.sqlite",
            execution_backend=FakeSandboxBackend(),
        )

    def tearDown(self) -> None:
        self.app.shutdown()
        self.tmp.cleanup()

    def _serve(self, **kwargs):
        server = make_http_server(self.app, port=0, **kwargs)
        self.addCleanup(server.server_close)
        return server

    def test_the_local_default_refuses_a_non_loopback_bind(self) -> None:
        """The reported bypass: build_control_app + host='0.0.0.0' served a full
        unauthenticated surface off-machine without naming any override."""
        for host in ("0.0.0.0", "::", "192.168.1.10"):
            with self.subTest(host=host), self.assertRaises(ValidationError) as ctx:
                make_http_server(self.app, host=host, port=0)
            self.assertIn(host, str(ctx.exception))

    def test_the_loopback_local_default_still_composes(self) -> None:
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                server = self._serve(host=host)
                self.assertEqual(server.server_address[0], host)

    def test_a_non_loopback_hosted_surface_makes_the_auth_decision(self) -> None:
        """Naming the hosted surface routes the wrapper through the same gate:
        no verifier and no override is a refusal, not an open plane."""
        with self.assertRaises(ValidationError) as ctx:
            make_http_server(
                self.app, host="0.0.0.0", port=0, surface_policy=HOSTED, env={}
            )
        self.assertIn(
            "refuses to serve an unauthenticated surface", str(ctx.exception)
        )

    def test_the_hosted_override_is_threaded_through_to_the_gate(self) -> None:
        """And the deliberate, loudly logged escape still works — proving the
        wrapper threads env into the decision rather than bypassing it."""
        with self.assertLogs("merv.brain.surface.auth", level="WARNING") as logs:
            server = self._serve(
                host="0.0.0.0",
                surface_policy=HOSTED,
                env={"MERV_ALLOW_OPEN_CONTROL": "1"},
            )
        self.assertEqual(server.server_address[0], "0.0.0.0")
        self.assertIn("OPEN CONTROL PLANE", "\n".join(logs.output))

    def test_loopback_classification(self) -> None:
        self.assertTrue(all(map(is_loopback_host, ("", "127.0.0.1", "127.5.5.5", "::1", "localhost", "[::1]"))))
        self.assertFalse(any(map(is_loopback_host, ("0.0.0.0", "::", "10.0.0.1", "example.com"))))


if __name__ == "__main__":
    unittest.main()
