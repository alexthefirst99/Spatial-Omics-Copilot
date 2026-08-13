from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

from evaluation.metrics import (
    compare_expected_tools,
    compute_automatic_metrics,
    compute_grounding_metrics,
    summarize_tool_calls,
)
from evaluation.full_run import build_full_metrics, discover_demo_assets
from evaluation.runner import load_cases, run_evaluation
from rag.contracts import AgentResult, TraceStep
from rag.copilot_agent.routing import plan_tools


def _fake_agent(**kwargs):
    return AgentResult(
        answer="",
        trace=[
            TraceStep(step="Loaded region gene expression", detail="1 gene"),
            TraceStep(
                step="Gene annotation", tool="gene_annotation_tool", status="ok",
                input_summary="EPCAM", output_summary="annotated 1 gene(s)",
            ),
        ],
        context_str="\n\n=== SPATIAL TRANSCRIPTOMICS EVIDENCE ===\nEPCAM\n=== END EVIDENCE ===",
        gene_objects=list(kwargs["deg"]),
        label=kwargs["label"],
        intent="gene_function",
        tools_called=["gene_annotation_tool"],
        tool_outcomes={
            "gene_annotation_tool": {
                "status": "ok",
                "input_summary": "EPCAM",
                "output_summary": "annotated 1 gene(s)",
                "result": {"genes": [{"gene_symbol": "EPCAM", "functional_summary": "cell adhesion"}]},
            }
        },
    )


def _fake_model(messages, provider, model):
    assert "EPCAM" in messages[0]["content"]
    return "EPCAM is supported by the retrieved NCBI Gene evidence."


def _config() -> dict:
    return {
        "generation": {"provider": "ollama", "model": "fake"},
        "cases": [{
            "roi_id": "roi-1",
            "label": "Synthetic ROI",
            "case_kind": "synthetic_benchmark",
            "source": {"type": "inline", "gene_objects": [{"gene": "EPCAM", "log2_fold_change": 2.0}]},
            "queries": [{
                "query_id": "q1", "category": "gene_lookup",
                "query": "What does EPCAM do?", "expected_route": "gene_function",
                "expected_tools": ["gene_annotation_tool"],
            }],
        }],
    }


def test_evaluation_runner_executes_one_case_and_writes_outputs(tmp_path):
    config_path = tmp_path / "cases.json"
    config_path.write_text(json.dumps(_config()), encoding="utf-8")
    output_dir = tmp_path / "outputs"

    records = run_evaluation(
        config_path, output_dir, agent_runner=_fake_agent, model_runner=_fake_model,
    )

    assert len(records) == 1
    record = records[0]
    assert record["schema_version"] == "2.0"
    assert record["case_id"] == "q1"
    assert record["category"] == "gene_lookup"
    assert record["expected_route"] == record["actual_route"] == "gene_function"
    assert record["expected_tools"] == record["tools_actually_called"]
    assert record["tool_calls"][0]["result_count"] == 1
    assert record["tool_calls"][0]["latency_seconds"] is None
    assert record["llm"]["calls"] == 1
    assert record["final_answer"]
    assert record["total_response_time_seconds"] >= 0
    assert record["structured_agent_trace"][-1]["step"] == "Synthesized final response"
    assert record["errors"] == []

    expected = {
        "raw_results.jsonl", "human_review.csv", "business_metrics_review.csv",
        "evaluation_summary.md", "metrics_summary.csv", "agentic_rag_workflow.md",
    }
    assert expected <= {path.name for path in output_dir.iterdir()}
    raw = json.loads((output_dir / "raw_results.jsonl").read_text().splitlines()[0])
    assert raw["final_answer"] == record["final_answer"]
    assert raw["tool_results_summary"][0]["evidence_nonempty"] is True

    with (output_dir / "human_review.csv").open(newline="", encoding="utf-8") as handle:
        review = next(csv.DictReader(handle))
    assert review["category"] == "gene_lookup"
    assert review["answer"] == record["answer"]
    assert review["biological_correctness_1_5"] == ""
    assert review["hallucination_1_5"] == ""


def test_checked_in_config_has_target_size_and_categories():
    payload = load_cases(Path("evaluation/eval_cases.json"))
    queries = [query for case in payload["cases"] for query in case["queries"]]
    counts = Counter(query["category"] for query in queries)

    assert 25 <= len(queries) <= 35
    assert counts == {
        "gene_lookup": 5,
        "pathway_enrichment": 5,
        "literature_retrieval": 5,
        "multi_step_interpretation": 8,
        "negative_failure_handling": 5,
    }
    assert all("expected_route" in query and "expected_tools" in query for query in queries)


def test_checked_in_expectations_match_the_production_planner():
    payload = load_cases(Path("evaluation/eval_cases.json"))

    for case in payload["cases"]:
        genes = [row["gene"] for row in case["source"].get("gene_objects", [])]
        for query in case["queries"]:
            plan = plan_tools(
                query["query"],
                genes=genes,
                has_genes=bool(genes),
                has_roi_image=bool(case["source"].get("roi_image_path")),
            )
            assert plan.intent == query["expected_route"], query["query_id"]
            assert list(plan.tools) == query["expected_tools"], query["query_id"]


def test_checked_in_synthetic_fixtures_do_not_leak_labels_or_claim_images():
    payload = load_cases(Path("evaluation/eval_cases.json"))
    answer_bearing_terms = {"epithelial", "immune", "proliferative", "stromal", "mixed", "ambiguous"}

    labels = []
    for case in payload["cases"]:
        labels.append(case["label"])
        assert not (answer_bearing_terms & set(case["label"].casefold().replace("/", " ").split()))
        assert not case["source"].get("roi_image_path")

        genes = [row["gene"] for row in case["source"].get("gene_objects", [])]
        assert len(genes) == len(set(genes))
        assert all(set(row) == {"gene", "log2_fold_change"} for row in case["source"].get("gene_objects", []))

    assert len(labels) == len(set(labels)) == 10


def test_case_parser_rejects_unknown_route_tool_and_category(tmp_path):
    config = _config()
    query = config["cases"][0]["queries"][0]
    query["expected_route"] = "invented_route"
    path = tmp_path / "bad-route.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown production route"):
        load_cases(path)

    config = _config()
    config["cases"][0]["queries"][0]["expected_tools"] = ["fake_tool"]
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown production tool"):
        load_cases(path)

    config = _config()
    config["cases"][0]["queries"][0]["category"] = "clinical_validation"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported evaluation category"):
        load_cases(path)


def test_expected_tool_comparison_reports_recall_and_unexpected_calls():
    metrics = compare_expected_tools(
        ["pathway_tool", "pubmed_tool"],
        ["gene_annotation_tool", "pathway_tool"],
    )
    assert metrics["expected_tool_recall"] == 0.5
    assert metrics["unexpected_tool_rate"] == 0.5
    assert metrics["appropriate_tool_call"] is False
    assert metrics["missing_expected_tools"] == ["pubmed_tool"]


def test_tool_summary_distinguishes_success_empty_and_failure():
    outcomes = {
        "gene_annotation_tool": {"status": "ok", "result": {"genes": [{"gene_symbol": "EPCAM"}]}},
        "pathway_tool": {"status": "empty", "result": {"pathways": []}},
        "pubmed_tool": {"status": "error", "error": "timeout", "result": None},
    }
    calls = summarize_tool_calls(outcomes, outcomes)
    by_tool = {call["tool"]: call for call in calls}
    assert by_tool["gene_annotation_tool"]["success"] is True
    assert by_tool["gene_annotation_tool"]["result_count"] == 1
    assert by_tool["pathway_tool"]["status"] == "empty"
    assert by_tool["pathway_tool"]["failure"] is False
    assert by_tool["pubmed_tool"]["failure"] is True
    assert by_tool["pubmed_tool"]["error"] == "timeout"


def _grounding_record(answer: str, *, negative: bool = False) -> dict:
    return {
        "final_answer": answer,
        "expect_insufficient_evidence": negative,
        "gene_image_evidence": {
            "gene_evidence": [{"gene": "EPCAM"}],
            "gene_annotations": {"genes": [{"gene_symbol": "EPCAM"}]},
        },
        "pathway_results": {
            "pathways": [{"name": "epithelial cell differentiation", "overlap_genes": ["EPCAM"]}],
        },
        "pubmed_results": {
            "papers": [{"pmid": "12345678", "title": "EPCAM in tumor epithelium", "abstract": "EPCAM evidence."}],
        },
    }


def test_grounding_metric_rewards_only_retrieved_evidence_and_citations():
    grounded = compute_grounding_metrics(
        _grounding_record("EPCAM supports epithelial cell differentiation [1] (PMID: 12345678).")
    )
    assert grounded["evidence_grounding_score"] == 1.0
    assert grounded["unsupported_genes"] == []
    assert grounded["unsupported_pmids"] == []

    unsupported = compute_grounding_metrics(
        _grounding_record("MKI67 proves proliferation [2] (PMID: 99999999).")
    )
    assert unsupported["evidence_grounding_score"] < grounded["evidence_grounding_score"]
    assert "MKI67" in unsupported["unsupported_genes"]
    assert unsupported["unsupported_pmids"] == ["99999999"]


def test_negative_grounding_requires_insufficient_evidence_acknowledgement():
    safe = compute_grounding_metrics(
        _grounding_record("Evidence is insufficient; EPCAM alone cannot determine a state.", negative=True)
    )
    unsafe = compute_grounding_metrics(
        _grounding_record("EPCAM definitively proves an aggressive tumor state.", negative=True)
    )
    assert safe["insufficient_evidence_acknowledged"] is True
    assert unsafe["insufficient_evidence_acknowledged"] is False
    assert safe["evidence_grounding_score"] > unsafe["evidence_grounding_score"]


def test_automatic_metrics_keep_tool_failure_separate_from_route_and_grounding():
    record = _grounding_record("EPCAM is supported by the ROI evidence [1].")
    record.update({
        "expected_route": "literature", "actual_route": "literature",
        "expected_tools": ["pubmed_tool"], "tools_actually_called": ["pubmed_tool"],
        "tool_calls": [{"tool": "pubmed_tool", "status": "error", "evidence_nonempty": False}],
        "errors": [], "llm": {"status": "ok"}, "total_response_time_seconds": 0.1,
    })
    metrics = compute_automatic_metrics(record)
    assert metrics["expected_route_match"] is True
    assert metrics["expected_tool_recall"] == 1.0
    assert metrics["tool_success_rate"] == 0.0
    assert metrics["external_service_failure_rate"] == 1.0
    assert metrics["level_1_infrastructure"]["run_completed"] is True


def test_runner_records_optional_tool_failure_without_failing_whole_run(tmp_path):
    def failing_tool_agent(**kwargs):
        return AgentResult(
            trace=[TraceStep(
                step="Gene annotation unavailable", tool="gene_annotation_tool",
                status="error", output_summary="NCBI Gene unavailable: timeout",
            )],
            context_str="\n\nGene annotation was unavailable this turn.",
            gene_objects=list(kwargs["deg"]), label=kwargs["label"], intent="gene_function",
            tools_called=["gene_annotation_tool"],
            tool_outcomes={
                "gene_annotation_tool": {
                    "status": "error", "error": "TimeoutError: timed out",
                    "output_summary": "NCBI Gene unavailable: timeout", "result": None,
                }
            },
        )

    path = tmp_path / "cases.json"
    path.write_text(json.dumps(_config()), encoding="utf-8")
    output = tmp_path / "out"
    record = run_evaluation(
        path, output, agent_runner=failing_tool_agent,
        model_runner=lambda *_: "The gene service was unavailable, so the function cannot be verified.",
    )[0]

    assert record["errors"] == []
    assert record["run_status"] == "partial"
    assert record["tool_errors"] == [{
        "tool": "gene_annotation_tool", "error": "TimeoutError: timed out",
    }]
    assert record["automatic_metrics"]["tool_success_rate"] == 0.0
    assert record["automatic_metrics"]["external_service_failure_rate"] == 1.0
    summary = (output / "evaluation_summary.md").read_text(encoding="utf-8")
    assert "`q1`: gene_annotation_tool: TimeoutError: timed out" in summary


def test_summary_contains_levels_category_breakdown_and_call_counts(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(_config()), encoding="utf-8")
    output = tmp_path / "out"
    run_evaluation(path, output, agent_runner=_fake_agent, model_runner=_fake_model)

    summary = (output / "evaluation_summary.md").read_text(encoding="utf-8")
    assert "Level 1 — Infrastructure" in summary
    assert "Level 2 — Agent behavior" in summary
    assert "Level 3 — Answer quality" in summary
    assert "not a clinical benchmark" in summary
    assert "NCBI Gene calls: 1" in summary

    with (output / "metrics_summary.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert any(row["scope"] == "overall" and row["metric"] == "router_accuracy" for row in rows)
    assert any(row["scope"] == "category" and row["category"] == "gene_lookup" for row in rows)


def test_no_generation_is_skipped_without_becoming_runtime_error(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(_config()), encoding="utf-8")
    records = run_evaluation(
        path, tmp_path / "out", generation_enabled=False,
        agent_runner=_fake_agent, model_runner=_fake_model,
    )
    assert records[0]["llm"]["status"] == "skipped"
    assert records[0]["llm"]["calls"] == 0
    assert records[0]["errors"] == []
    assert records[0]["run_status"] == "partial"


def test_full_run_discovers_the_default_demo_asset_shapes(tmp_path):
    h5ad = tmp_path / "sample_feature_slice.h5ad"
    image = tmp_path / "sample_image.tif"
    h5ad.write_bytes(b"h5ad")
    image.write_bytes(b"tiff")

    manifest = discover_demo_assets(tmp_path)

    assert manifest["h5ad"]["path"] == str(h5ad)
    assert manifest["image"]["path"] == str(image)
    assert manifest["h5ad"]["size_bytes"] == 4
    assert manifest["benchmark_case_source"] == "synthetic_inline_fixtures"
    assert manifest["demo_assets_used_for_case_inputs"] is False


def test_full_metrics_json_includes_aggregate_category_and_contract_checks():
    record = {
        "case_id": "q1",
        "category": "gene_lookup",
        "expected_route": "gene_function",
        "actual_route": "gene_function",
        "expected_tools": ["gene_annotation_tool"],
        "tools_actually_called": ["gene_annotation_tool"],
        "tool_calls": [{
            "tool": "gene_annotation_tool", "status": "ok",
            "evidence_nonempty": True,
        }],
        "tool_errors": [],
        "errors": [],
        "final_answer": "EPCAM is supported by retrieved evidence.",
        "automatic_metrics": {
            "expected_route_match": True,
            "expected_tool_recall": 1.0,
            "appropriate_tool_call": True,
            "unexpected_tool_rate": 0.0,
            "evidence_grounding_score": 1.0,
        },
        "llm": {"calls": 1},
        "total_response_time_seconds": 0.1,
    }

    metrics = build_full_metrics(
        [record], provider="deepinfra", model="model",
        demo_assets={"data_dir": "/demo"},
        config_path=Path("evaluation/eval_cases.json"),
    )

    assert metrics["overall"]["router_accuracy"] == 1.0
    assert metrics["by_category"]["gene_lookup"]["total_cases"] == 1
    assert metrics["contract_checks"]["route_mismatch_count"] == 0
    assert metrics["contract_checks"]["exact_tool_set_mismatch_count"] == 0
    assert metrics["quality_coverage"]["average_evidence_grounding_score"] == 1.0
    assert metrics["per_case_automatic_metrics"]["q1"]["expected_route_match"] is True
