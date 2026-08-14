from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluation import judges, metrics, reporting, runner


def _record(roi_id: str, *, answer: str = "answer", seconds: float = 10.0) -> dict:
    return {
        "roi_id": roi_id,
        "roi": {"center": [1.0, 2.0], "bounds": [0, 0, 2, 2], "spot_count": 100},
        "answer": answer,
        "agent": {"status": "completed" if answer else "error", "tool_call_count": 0},
        "timing": {"copilot_end_to_end_seconds": seconds},
        "workflow_efficiency": {
            "automatically_connected_stage_count": 0,
            "manual_data_reentry_steps": 0,
        },
        "agent_trace_validation": {"valid": False},
        "judgments": {"status": "skipped", "text": {}, "vision": {}, "errors": []},
        "errors": [] if answer else ["failed"],
    }


class JudgeFailureTests(unittest.TestCase):
    def test_model_runner_exception_is_returned_as_judge_error(self) -> None:
        fake_inference = types.ModuleType("app.inference")
        fake_inference.is_inference_error = lambda text: False
        fake_app = types.ModuleType("app")
        fake_app.inference = fake_inference

        def boom(*args, **kwargs):
            raise RuntimeError("provider unavailable")

        client = judges.JudgeClient(boom, "deepinfra", "model")
        with patch.dict(sys.modules, {"app": fake_app, "app.inference": fake_inference}):
            payload, raw_outputs, error = client.ask("return json")

        self.assertIsNone(payload)
        self.assertEqual(raw_outputs, [])
        self.assertIn("RuntimeError: provider unavailable", error)


class MetricCorrectnessTests(unittest.TestCase):
    def test_failed_roi_time_is_not_counted_as_successful_response_time(self) -> None:
        result = metrics.aggregate_proposal_metrics([
            _record("real_roi_01", answer="ok", seconds=10.0),
            _record("real_roi_02", answer="", seconds=999.0),
        ])
        self.assertEqual(result["technical"]["response_time"]["mean_seconds"], 10.0)
        self.assertEqual(result["coverage"]["answer_generated_rois"], 1)
        self.assertEqual(result["coverage"]["attempted_rois"], 2)


class RunnerIsolationTests(unittest.TestCase):
    def test_unhandled_roi_exception_does_not_stop_remaining_rois(self) -> None:
        rois = [
            {"roi_id": f"real_roi_{i:02d}", "spot_count": 100, "center": [i, i], "bounds": [0, 0, 1, 1]}
            for i in range(1, 11)
        ]
        call_count = 0

        def fake_execute(roi, **kwargs):
            nonlocal call_count
            call_count += 1
            if roi["roi_id"] == "real_roi_02":
                raise RuntimeError("judge/network crash")
            return _record(roi["roi_id"], answer="ok", seconds=float(call_count))

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "outputs"
            with (
                patch.object(runner, "load_cases", return_value={}),
                patch.object(runner, "_resolve_provider_model", return_value=("deepinfra", "model")),
                patch.object(runner, "generate_real_rois", return_value=(rois, {})),
                patch.object(runner, "execute_roi", side_effect=fake_execute),
            ):
                records = runner.run_evaluation("dummy.json", output)

            self.assertEqual(len(records), 10)
            self.assertEqual(call_count, 10)
            self.assertEqual(records[1]["agent"]["status"], "error")
            self.assertIn("Unhandled ROI failure", records[1]["errors"][0])
            summary = (output / "summary.md").read_text(encoding="utf-8")
            self.assertIn("10/10 ROI attempts persisted", summary)


class ReportingCoverageTests(unittest.TestCase):
    def test_partial_checkpoint_is_explicitly_marked_incomplete(self) -> None:
        records = [_record("real_roi_01")]
        aggregate = metrics.aggregate_proposal_metrics(records)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            reporting.write_outputs(
                output,
                records,
                aggregate,
                {"expected_roi_count": 10, "dataset": {}, "provider": "deepinfra", "model": "model"},
            )
            summary = (output / "summary.md").read_text(encoding="utf-8")
            self.assertIn("INCOMPLETE", summary)
            self.assertIn("1/10 ROI attempts persisted", summary)


if __name__ == "__main__":
    unittest.main()
