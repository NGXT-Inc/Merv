from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from backend.dataplane.sandbox_outputs import pull_sandbox_outputs
from backend.utils import ValidationError


class SandboxOutputPullTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _sandbox(self) -> dict:
        return {
            "status": "running",
            "experiment_id": "exp_1",
            "sandbox_uid": "uid_123",
            "sandbox_id": "sb_1",
            "experiment_dir": "/workspace/experiments/exp-one",
            "local_experiment_dir": str(self.repo / "experiments" / "exp-one"),
            "ssh": {
                "host": "sandbox.example",
                "port": 2222,
                "user": "root",
                "key_path": str(self.repo / "key"),
            },
        }

    def test_default_pull_discovers_common_existing_outputs(self) -> None:
        calls: list[list[str]] = []

        def runner(command, **_kwargs):
            calls.append(list(command))
            if command[0] == "ssh":
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="report.md\nresults/\n",
                    stderr="",
                )
            if command[0] == "rsync":
                source = str(command[-2])
                destination = Path(str(command[-1]))
                if "report.md" in source:
                    (destination / "report.md").write_text(
                        "## Summary\nRetained.\n",
                        encoding="utf-8",
                    )
                elif "results/" in source:
                    (destination / "metrics.json").write_text(
                        '{"accuracy": 0.72}\n',
                        encoding="utf-8",
                    )
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            raise AssertionError(command)

        result = pull_sandbox_outputs(
            repo_root=self.repo,
            sandbox=self._sandbox(),
            runner=runner,
        )

        self.assertTrue(result["defaulted"])
        self.assertEqual(result["paths_requested"], ["report.md", "results/"])
        self.assertEqual(result["paths_pulled"], ["report.md", "results/"])
        self.assertEqual(result["destination_path"], "experiments/exp-one")
        self.assertEqual(result["files_present"], 2)
        self.assertGreater(result["bytes_present"], 0)
        self.assertEqual(calls[0][0], "ssh")
        self.assertEqual([call[0] for call in calls[1:]], ["rsync", "rsync"])
        self.assertIn("--ignore-existing", calls[1])
        self.assertTrue((self.repo / "experiments" / "exp-one" / "report.md").exists())
        self.assertTrue(
            (self.repo / "experiments" / "exp-one" / "results" / "metrics.json").exists()
        )

    def test_existing_file_requires_overwrite(self) -> None:
        target = self.repo / "experiments" / "exp-one" / "report.md"
        target.parent.mkdir(parents=True)
        target.write_text("local report\n", encoding="utf-8")

        with self.assertRaisesRegex(ValidationError, "overwrite=true"):
            pull_sandbox_outputs(
                repo_root=self.repo,
                sandbox=self._sandbox(),
                paths=["report.md"],
                runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("runner should not be called")
                ),
            )

    def test_rejects_paths_that_escape_repo_semantics(self) -> None:
        with self.assertRaisesRegex(ValidationError, "may not contain"):
            pull_sandbox_outputs(
                repo_root=self.repo,
                sandbox=self._sandbox(),
                paths=["../secret.txt"],
                runner=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    AssertionError("runner should not be called")
                ),
            )


if __name__ == "__main__":
    unittest.main()
