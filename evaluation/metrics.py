"""Aggregation for the proposal's seven technical and five business metrics."""

from __future__ import annotations

import statistics
from typing import Any, Iterable


TOOL_RESULT_KEYS = {
    "gene_annotation_tool": "genes",
    "pathway_tool": "pathways",
    "pubmed_tool": "papers",
}
TOOL_SERVICE_NAMES = {
    "gene_annotation_tool": "ncbi_gene",
    "pathway_tool": "enrichr",
    "pubmed_tool": "pubmed",
}


def _mean(values: Iterable[Any]) -> float | None:
    numbers = [
        float(value)
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    return statistics.fmean(numbers) if numbers else None


def _result_count(tool: str, outcome: dict[str, Any]) -> int:
    payload = outcome.get("result")
    key = TOOL_RESULT_KEYS.get(tool)
    return len(payload.get(key) or []) if key and isinstance(payload, dict) else 0


def summarize_tool_calls(
    tools_called: Iterable[str],
    tool_outcomes: dict[str, Any] | None,
    trace: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Serialize production tool outcomes without inventing missing timings."""

    outcomes = tool_outcomes or {}
    trace_by_tool = {
        str(step.get("tool")): step
        for step in trace
        if isinstance(step, dict) and step.get("tool") in TOOL_RESULT_KEYS
    }
    ordered_tools = list(dict.fromkeys([*tools_called, *outcomes]))
    calls: list[dict[str, Any]] = []
    for order, tool in enumerate(ordered_tools, start=1):
        outcome = outcomes.get(tool) if isinstance(outcomes.get(tool), dict) else {}
        step = trace_by_tool.get(tool, {})
        status = str(outcome.get("status") or step.get("status") or "unknown")
        count = _result_count(tool, outcome)
        calls.append({
            "order": order,
            "tool": tool,
            "service": TOOL_SERVICE_NAMES.get(tool, tool),
            "status": status,
            "success": status in {"ok", "supplied"},
            "failure": status == "error",
            "result_count": count,
            "evidence_nonempty": count > 0,
            "latency_seconds": outcome.get("latency_seconds"),
            "input_summary": str(
                outcome.get("input_summary") or step.get("input_summary") or ""
            ),
            "output_summary": str(
                outcome.get("output_summary") or step.get("output_summary") or ""
            ),
            "error": str(outcome.get("error") or ""),
        })
    return calls


def _all_judged(records: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    return [
        item
        for record in records
        for item in (
            ((record.get("judgments") or {}).get("text") or {}).get(field) or []
        )
    ]


def _scores(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        ((record.get("judgments") or {}).get("text") or {}).get("scores") or {}
        for record in records
    ]


def _quality_metric(scores: list[dict[str, Any]]) -> dict[str, Any]:
    means = {
        name: _mean(score.get(name) for score in scores)
        for name in (
            "biological_reasonableness",
            "roi_specificity",
            "clarity_understandability",
        )
    }
    overall = _mean(means.values())
    complete = all(value is not None for value in means.values())
    display = "N/A (incomplete quality judgments)"
    if complete and overall is not None:
        display = (
            f"biological reasonableness {means['biological_reasonableness']:.2f}/5; "
            f"ROI specificity {means['roi_specificity']:.2f}/5; "
            f"clarity {means['clarity_understandability']:.2f}/5; "
            f"overall {overall:.2f}/5"
        )
    return {**means, "overall_mean": overall, "display": display}


def aggregate_proposal_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return exactly the 7 technical and 5 business metrics requested."""

    pubmed = _all_judged(records, "pubmed")
    pathways = _all_judged(records, "pathways")
    claims = _all_judged(records, "claims")
    scores = _scores(records)
    vision = [
        verdict
        for record in records
        if (verdict := (
            (record.get("judgments") or {}).get("vision") or {}
        ).get("verdict")) in {"PASS", "FAIL"}
    ]
    mentioned_genes = [
        gene
        for record in records
        for gene in (
            ((record.get("judgments") or {}).get("text") or {}).get(
                "mentioned_genes"
            ) or []
        )
    ]
    unsupported_genes = [
        gene
        for record in records
        for gene in (
            ((record.get("judgments") or {}).get("text") or {}).get(
                "unsupported_mentioned_genes"
            ) or []
        )
    ]
    elapsed = [
        value
        for record in records
        if isinstance(
            (value := (record.get("timing") or {}).get(
                "copilot_end_to_end_seconds"
            )),
            (int, float),
        )
    ]

    pubmed_relevant = sum(item.get("label") == "relevant" for item in pubmed)
    pathway_relevant = sum(item.get("label") == "relevant" for item in pathways)
    supported = sum(item.get("label") == "supported" for item in claims)
    unsupported = sum(item.get("label") == "unsupported" for item in claims)
    image_passes = sum(verdict == "PASS" for verdict in vision)

    response_time = {
        "mean_seconds": _mean(elapsed),
        "median_seconds": statistics.median(elapsed) if elapsed else None,
        "min_seconds": min(elapsed) if elapsed else None,
        "max_seconds": max(elapsed) if elapsed else None,
        "display": "N/A",
    }
    if elapsed:
        response_time["display"] = (
            f"mean {response_time['mean_seconds']:.2f}s; "
            f"median {response_time['median_seconds']:.2f}s; "
            f"min {response_time['min_seconds']:.2f}s; "
            f"max {response_time['max_seconds']:.2f}s"
        )

    technical = {
        "pubmed_retrieval_relevance": {
            "relevant": pubmed_relevant,
            "retrieved": len(pubmed),
            "precision_at_k": pubmed_relevant / len(pubmed) if pubmed else None,
            "display": (
                f"{pubmed_relevant}/{len(pubmed)} relevant; "
                f"Precision@k={100 * pubmed_relevant / len(pubmed):.1f}%"
                if pubmed else "N/A (no papers judged)"
            ),
        },
        "pathway_relevance": {
            "relevant": pathway_relevant,
            "evaluated": len(pathways),
            "rate": pathway_relevant / len(pathways) if pathways else None,
            "display": (
                f"{pathway_relevant}/{len(pathways)} relevant "
                f"({100 * pathway_relevant / len(pathways):.1f}%)"
                if pathways else "N/A (no pathways judged)"
            ),
        },
        "image_to_gene_connection": {
            "passes": image_passes,
            "evaluated_rois": len(vision),
            "pass_rate": image_passes / len(vision) if vision else None,
            "display": (
                f"{image_passes}/{len(vision)} PASS "
                f"({100 * image_passes / len(vision):.1f}%)"
                if vision else "N/A (no vision judgments)"
            ),
        },
        "groundedness": {
            "supported_claims": supported,
            "verifiable_claims": len(claims),
            "rate": supported / len(claims) if claims else None,
            "display": (
                f"{supported}/{len(claims)} supported "
                f"({100 * supported / len(claims):.1f}%)"
                if claims else "N/A (no verifiable claims judged)"
            ),
        },
        "hallucination_rate": {
            "unsupported_claims": unsupported,
            "verifiable_claims": len(claims),
            "rate": unsupported / len(claims) if claims else None,
            "unsupported_gene_names": len(unsupported_genes),
            "mentioned_gene_names": len(mentioned_genes),
            "gene_name_rate": (
                len(unsupported_genes) / len(mentioned_genes)
                if mentioned_genes else None
            ),
            "display": (
                f"claims {unsupported}/{len(claims)} "
                f"({100 * unsupported / len(claims):.1f}%); "
                if claims else "claims N/A; "
            ) + (
                f"gene names {len(unsupported_genes)}/{len(mentioned_genes)} "
                f"({100 * len(unsupported_genes) / len(mentioned_genes):.1f}%)"
                if mentioned_genes else "gene names N/A"
            ),
        },
        "answer_quality": _quality_metric(scores),
        "response_time": response_time,
    }

    time_saved = [14400.0 - value for value in elapsed]
    time_saved_percent = [value / 14400.0 * 100.0 for value in time_saved]
    stage_counts = [
        (record.get("workflow_efficiency") or {}).get(
            "automatically_connected_stage_count"
        )
        for record in records
    ]
    tool_counts = [
        (record.get("agent") or {}).get("tool_call_count") for record in records
    ]
    reentry_counts = [
        (record.get("workflow_efficiency") or {}).get(
            "manual_data_reentry_steps"
        )
        for record in records
    ]
    mean_stages = _mean(stage_counts)
    mean_tools = _mean(tool_counts)
    mean_reentry = _mean(reentry_counts)
    total_tools = sum(
        int(value) for value in tool_counts if isinstance(value, (int, float))
    )

    def score_metric(name: str) -> dict[str, Any]:
        value = _mean(score.get(name) for score in scores)
        return {
            "mean_score": value,
            "display": f"{value:.2f}/5 mean" if value is not None else "N/A",
        }

    trust = _mean(score.get("trust") for score in scores)
    adoption = _mean(score.get("adoption") for score in scores)
    business = {
        "time_saved": {
            "manual_baseline_seconds": 14400,
            "mean_time_saved_seconds": _mean(time_saved),
            "mean_time_saved_percent": _mean(time_saved_percent),
            "display": (
                f"{_mean(time_saved):.2f}s ({_mean(time_saved_percent):.2f}%) "
                "mean saved per ROI"
                if time_saved else "N/A"
            ),
        },
        "workflow_efficiency": {
            "mean_connected_stage_count": mean_stages,
            "total_tool_calls": total_tools,
            "mean_tool_calls": mean_tools,
            "mean_manual_data_reentry_steps": mean_reentry,
            "display": (
                f"{mean_stages:.0f} connected analysis/retrieval stages/ROI; "
                f"{total_tools} tool calls total ({mean_tools:.1f}/ROI); "
                f"{mean_reentry:.0f} manual data re-entry steps/ROI"
                if None not in (mean_stages, mean_tools, mean_reentry) else "N/A"
            ),
        },
        "hypothesis_usefulness": score_metric("hypothesis_usefulness"),
        "decision_quality": score_metric("decision_quality"),
        "trust_and_adoption": {
            "trust_mean": trust,
            "adoption_mean": adoption,
            "display": (
                f"Trust {trust:.2f}/5; Adoption {adoption:.2f}/5"
                if trust is not None and adoption is not None else "N/A"
            ),
        },
    }
    return {"technical": technical, "business": business}


__all__ = ["aggregate_proposal_metrics", "summarize_tool_calls"]
