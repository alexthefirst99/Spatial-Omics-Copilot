from __future__ import annotations

import json
from pathlib import Path

from evaluation.judges import JudgeClient, _parse_json
from evaluation.metrics import aggregate_proposal_metrics, summarize_tool_calls
from evaluation.reporting import write_outputs
from evaluation.runner import _model_error, generate_real_rois, load_cases


def test_config_defines_ten_seeded_real_rois_without_synthetic_genes():
    config = load_cases(Path("evaluation/eval_cases.json"))

    assert config["roi_generation"]["count"] == 10
    assert config["roi_generation"]["seed"] == 42
    assert config["roi_generation"]["roi_size_pixels"] == 256
    assert config["roi_generation"]["min_spots"] == 100
    assert "gene_objects" not in json.dumps(config)


def test_real_roi_generation_is_deterministic_and_spot_centered(tmp_path):
    config_path = Path("evaluation/eval_cases.json").resolve()
    config = load_cases(config_path)

    first, first_metadata = generate_real_rois(config, config_path, tmp_path)
    second, second_metadata = generate_real_rois(config, config_path, tmp_path)

    assert first == second
    assert len(first) == 10
    assert first_metadata["roi_seed"] == 42
    assert second_metadata["roi_cache_reused"] is True
    assert all(roi["center_spot_barcode"] for roi in first)
    assert all(roi["spot_count"] >= 100 for roi in first)
    assert all(
        round(roi["bounds"][2] - roi["bounds"][0]) == 256
        and round(roi["bounds"][3] - roi["bounds"][1]) == 256
        for roi in first
    )


def test_judge_client_retries_invalid_json_once():
    responses = iter(["truncated {", '{"verdict":"PASS"}'])
    client = JudgeClient(
        model_runner=lambda messages, provider, model: next(responses),
        provider="fake",
        model="fake",
    )

    payload, raw_outputs, error = client.ask("judge")

    assert payload == {"verdict": "PASS"}
    assert len(raw_outputs) == 2
    assert error == ""
    assert _parse_json('```json\n{"verdict":"FAIL"}\n```')["verdict"] == "FAIL"


def test_judge_client_does_not_retry_provider_errors():
    calls = 0

    def failed_model_runner(messages, provider, model):
        nonlocal calls
        calls += 1
        return "DeepInfra returned HTTP 404."

    client = JudgeClient(
        model_runner=failed_model_runner,
        provider="deepinfra",
        model="missing-model",
    )

    payload, raw_outputs, error = client.ask("judge")

    assert payload is None
    assert raw_outputs == ["DeepInfra returned HTTP 404."]
    assert error == "DeepInfra returned HTTP 404."
    assert calls == 1
    assert _model_error(error) is True


def test_tool_summary_preserves_production_status_and_missing_timing():
    calls = summarize_tool_calls(
        ["pubmed_tool"],
        {"pubmed_tool": {"status": "ok", "result": {"papers": [{"pmid": "1"}]}}},
        [{"tool": "pubmed_tool", "status": "ok"}],
    )

    assert calls == [{
        "order": 1,
        "tool": "pubmed_tool",
        "service": "pubmed",
        "status": "ok",
        "success": True,
        "failure": False,
        "result_count": 1,
        "evidence_nonempty": True,
        "latency_seconds": None,
        "input_summary": "",
        "output_summary": "",
        "error": "",
    }]


def _complete_record() -> dict:
    return {
        "roi_id": "real_roi_01",
        "roi": {
            "center": [100, 200],
            "bounds": [0, 100, 256, 356],
            "spot_count": 500,
            "crop_path": "crop.png",
        },
        "timing": {"copilot_end_to_end_seconds": 100.0},
        "workflow_efficiency": {
            "automatically_connected_stage_count": 5,
            "manual_data_reentry_steps": 0,
        },
        "agent": {"tool_call_count": 3, "status": "completed"},
        "agent_trace_validation": {"valid": True},
        "judgments": {
            "text": {
                "pubmed": [{"id": "P1", "label": "relevant"}],
                "pathways": [{"id": "W1", "label": "relevant"}],
                "claims": [{"claim": "claim", "label": "supported"}],
                "mentioned_genes": ["HBA1"],
                "unsupported_mentioned_genes": [],
                "scores": {
                    "biological_reasonableness": 4,
                    "roi_specificity": 4,
                    "clarity_understandability": 4,
                    "hypothesis_usefulness": 4,
                    "decision_quality": 4,
                    "trust": 4,
                    "adoption": 4,
                },
            },
            "vision": {"verdict": "PASS"},
            "errors": [],
        },
        "errors": [],
    }


def test_metrics_and_reporter_emit_exact_required_outputs(tmp_path):
    records = [_complete_record()]
    metrics = aggregate_proposal_metrics(records)
    write_outputs(
        tmp_path,
        records,
        metrics,
        {
            "dataset": {},
            "provider": "fake",
            "model": "fake",
            "expected_roi_count": 1,
        },
    )

    assert set(metrics) == {"technical", "business", "coverage"}
    assert len(metrics["technical"]) == 7
    assert len(metrics["business"]) == 5
    assert {path.name for path in tmp_path.iterdir()} == {
        "summary.md",
        "technical_metrics.csv",
        "business_metrics.csv",
        "per_roi_results.csv",
        "raw_results.json",
    }
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "## Technical Metrics" in summary
    assert "## Business Metrics" in summary


def test_missing_judgments_remain_na():
    metrics = aggregate_proposal_metrics([{
        "timing": {"copilot_end_to_end_seconds": 10.0},
        "workflow_efficiency": {
            "automatically_connected_stage_count": 5,
            "manual_data_reentry_steps": 0,
        },
        "agent": {"tool_call_count": 3},
        "judgments": {"status": "skipped"},
    }])

    assert metrics["technical"]["pubmed_retrieval_relevance"]["precision_at_k"] is None
    assert metrics["technical"]["image_to_gene_connection"]["pass_rate"] is None
    assert metrics["business"]["time_saved"]["mean_time_saved_seconds"] is None
