from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.dataplane.results_tsv import merge_results_tsv
from backend.utils import ValidationError


class ResultsTsvMergeTest(unittest.TestCase):
    def test_appends_new_rows_and_skips_identical_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "results.tsv").write_text(
                "row_id\tmetric\tvalue\n"
                "a\taccuracy\t0.70\n",
                encoding="utf-8",
            )
            (repo / "incoming.tsv").write_text(
                "row_id\tmetric\tvalue\n"
                "a\taccuracy\t0.70\n"
                "b\taccuracy\t0.72\n",
                encoding="utf-8",
            )

            result = merge_results_tsv(
                repo_root=repo,
                source_path="incoming.tsv",
                target_path="results.tsv",
            )

            text = (repo / "results.tsv").read_text(encoding="utf-8")

        self.assertEqual(result["inserted_rows"], 1)
        self.assertEqual(result["skipped_rows"], 1)
        self.assertEqual(
            text,
            "row_id\tmetric\tvalue\n"
            "a\taccuracy\t0.70\n"
            "b\taccuracy\t0.72\n",
        )

    def test_refuses_conflicting_existing_rows_without_modifying_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            original = "row_id\tmetric\tvalue\n" "a\taccuracy\t0.70\n"
            (repo / "results.tsv").write_text(original, encoding="utf-8")
            (repo / "incoming.tsv").write_text(
                "row_id\tmetric\tvalue\n"
                "a\taccuracy\t0.71\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError) as ctx:
                merge_results_tsv(
                    repo_root=repo,
                    source_path="incoming.tsv",
                    target_path="results.tsv",
                )
            after = (repo / "results.tsv").read_text(encoding="utf-8")

        self.assertIn("conflict", str(ctx.exception))
        self.assertEqual(after, original)

    def test_requires_stable_key_columns_when_none_can_be_inferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "incoming.tsv").write_text(
                "metric\tvalue\naccuracy\t0.72\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError) as ctx:
                merge_results_tsv(
                    repo_root=repo,
                    source_path="incoming.tsv",
                    target_path="results.tsv",
                )

        self.assertIn("key_columns is required", str(ctx.exception))

    def test_rejects_malformed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "incoming.tsv").write_text(
                "row_id\tmetric\n"
                "a\taccuracy\textra\n",
                encoding="utf-8",
            )

            with self.assertRaises(ValidationError) as ctx:
                merge_results_tsv(
                    repo_root=repo,
                    source_path="incoming.tsv",
                    target_path="results.tsv",
                )

        self.assertIn("more fields than the header", str(ctx.exception))

    def test_dry_run_reports_counts_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "results.tsv").write_text(
                "row_id\tmetric\tvalue\n"
                "a\taccuracy\t0.70\n",
                encoding="utf-8",
            )
            (repo / "incoming.tsv").write_text(
                "row_id\tmetric\tvalue\n"
                "b\taccuracy\t0.72\n",
                encoding="utf-8",
            )

            result = merge_results_tsv(
                repo_root=repo,
                source_path="incoming.tsv",
                target_path="results.tsv",
                dry_run=True,
            )
            text = (repo / "results.tsv").read_text(encoding="utf-8")

        self.assertEqual(result["inserted_rows"], 1)
        self.assertEqual(result["target_rows_after"], 2)
        self.assertEqual(text, "row_id\tmetric\tvalue\n" "a\taccuracy\t0.70\n")


if __name__ == "__main__":
    unittest.main()
