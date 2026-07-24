"""Tests for hosted in-memory tool-call telemetry and shared rollups."""

from __future__ import annotations

import unittest

from merv.brain.kernel.state.tool_call_stats import percentile
from merv.brain.surface.control.control_runtime import ControlToolCallSink


class PercentileTest(unittest.TestCase):
    def test_inclusive_quantile(self) -> None:
        self.assertEqual(percentile([], 95), 0)
        self.assertEqual(percentile([42], 95), 42)
        self.assertEqual(percentile([1, 2, 3, 4], 50), 2)
        self.assertEqual(percentile(list(range(1, 101)), 95), 95)


class ControlToolCallSinkTest(unittest.TestCase):
    def test_redacts_sensitive_nested_result(self) -> None:
        sink = ControlToolCallSink()
        sink.record(
            tool="experiment.transition",
            source="mcp",
            status="ok",
            duration_ms=1,
            arguments={
                "project_id": "p",
                "reviewer_capability": "rp_arg",
            },
            result={
                "mlflow": {
                    "env": {"MLFLOW_TRACKING_PASSWORD": "rr_sk_agent"}
                }
            },
        )
        summary = sink.stats()["calls"][0]
        call = sink.get(call_id=summary["id"])
        self.assertIsNotNone(call)
        self.assertEqual(call["args"]["reviewer_capability"], "[redacted]")
        self.assertEqual(
            call["result"]["mlflow"]["env"]["MLFLOW_TRACKING_PASSWORD"],
            "[redacted]",
        )

    def test_stats_sort_filter_and_project_clear(self) -> None:
        sink = ControlToolCallSink()
        for project_id, size in (("p1", 5), ("p2", 50)):
            sink.record(
                tool="claim.list",
                source="mcp",
                status="ok",
                duration_ms=size,
                arguments={"project_id": project_id},
                result={"claims": ["x" * size]},
            )
        stats = sink.stats(project_ids={"p2"}, sort="received_chars")
        self.assertEqual(stats["totals"]["calls"], 1)
        self.assertEqual(stats["calls"][0]["project_id"], "p2")
        self.assertEqual(sink.clear(project_ids={"p1"}), {"cleared": 1})
        self.assertEqual(sink.stats()["totals"]["calls"], 1)

    def test_rollup_helpers_remain_single_sourced(self) -> None:
        from tests.paths import BACKEND_ROOT

        source = (
            BACKEND_ROOT / "surface" / "control" / "control_runtime.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("def _percentile", source)
        self.assertNotIn("def _by_tool", source)
        self.assertNotIn("def _accumulate", source)
        self.assertNotIn("def _finalize_bucket", source)
        self.assertIn("tool_call_stats import", source)


if __name__ == "__main__":
    unittest.main()
