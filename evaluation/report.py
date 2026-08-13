"""Review templates and aggregate benchmark reporting."""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path
from typing import Any, Iterable


HUMAN_FIELDS = [
    "case_id", "run_id", "category", "question", "answer", "reviewer_id",
    "biological_correctness_1_5", "evidence_grounding_1_5", "completeness_1_5",
    "hallucination_1_5", "usefulness_1_5", "reviewer_notes",
]

BUSINESS_FIELDS = [
    "case_id", "run_id", "category", "question", "reviewer_id",
    "estimated_manual_minutes", "estimated_copilot_minutes", "workflow_steps_saved",
    "would_researcher_trust_answer_1_5", "would_researcher_use_for_followup_1_5", "notes",
]

def _write_csv(path: Path, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _template_rows(records: list[dict[str, Any]], fields: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        row = {
            "case_id": record.get("case_id", ""),
            "run_id": record.get("run_id", ""),
            "category": record.get("category", ""),
            "question": record.get("question", record.get("query", "")),
            "answer": record.get("answer", record.get("final_answer", "")),
        }
        rows.append({field: row.get(field, "") for field in fields})
    return rows


def _ensure_template(path: Path, fields: list[str], records: list[dict[str, Any]]) -> None:
    new_rows = _template_rows(records, fields)
    if not path.exists():
        _write_csv(path, fields, new_rows)
        return

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        old_fields = reader.fieldnames or []
        old_rows = list(reader)

    if old_fields != fields:
        # The rubric changed from schema v1. Preserve prior reviews verbatim in
        # a sidecar before creating the new, non-fabricated template.
        backup = path.with_name(f"{path.stem}.schema_v1{path.suffix}")
        suffix = 2
        while backup.exists():
            backup = path.with_name(f"{path.stem}.schema_v1_{suffix}{path.suffix}")
            suffix += 1
        path.replace(backup)
        _write_csv(path, fields, new_rows)
        return

    existing = {
        str(row.get("case_id") or row.get("run_id") or ""): row
        for row in old_rows
    }
    merged: list[dict[str, Any]] = []
    for row in new_rows:
        key = str(row.get("case_id") or row.get("run_id") or "")
        prior = existing.get(key, {})
        # Refresh machine-generated context, retain all reviewer-entered fields.
        merged_row = dict(row)
        for field in fields:
            if field not in {"case_id", "run_id", "category", "question", "answer"}:
                merged_row[field] = prior.get(field, "")
        merged.append(merged_row)
    _write_csv(path, fields, merged)


def ensure_review_templates(output_dir: Path, records: list[dict[str, Any]]) -> None:
    """Create/update review templates without fabricating reviewer scores."""

    _ensure_template(output_dir / "human_review.csv", HUMAN_FIELDS, records)
    _ensure_template(output_dir / "business_metrics_review.csv", BUSINESS_FIELDS, records)


def _read_scores(path: Path, fields: list[str]) -> dict[str, list[float]]:
    scores = {field: [] for field in fields}
    if not path.exists():
        return scores
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            for field in fields:
                value = str(row.get(field, "")).strip()
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    continue
                scores[field].append(number)
    return scores


def _mean(values: Iterable[float]) -> float | None:
    values = list(values)
    return statistics.fmean(values) if values else None


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _fmt(value: Any, digits: int = 3) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    elapsed = [
        float(record["total_response_time_seconds"])
        for record in records if isinstance(record.get("total_response_time_seconds"), (int, float))
    ]
    route = [
        metrics.get("expected_route_match")
        for record in records
        if isinstance((metrics := record.get("automatic_metrics") or {}).get("expected_route_match"), bool)
    ]
    expected_recall = [
        float(value)
        for record in records
        if isinstance((value := (record.get("automatic_metrics") or {}).get("expected_tool_recall")), (int, float))
    ]
    appropriate = [
        bool(value)
        for record in records
        if isinstance((value := (record.get("automatic_metrics") or {}).get("appropriate_tool_call")), bool)
    ]
    grounding = [
        float(value)
        for record in records
        if record.get("final_answer")
        and isinstance((value := (record.get("automatic_metrics") or {}).get("evidence_grounding_score")), (int, float))
    ]
    calls = [call for record in records for call in (record.get("tool_calls") or []) if isinstance(call, dict)]
    return {
        "total_cases": len(records),
        "successful_runs": sum(not bool(record.get("errors")) for record in records),
        "runtime_error_rate": (
            sum(bool(record.get("errors")) for record in records) / len(records) if records else None
        ),
        "router_accuracy": _mean(float(value) for value in route),
        "expected_tool_recall": _mean(expected_recall),
        "appropriate_tool_call_rate": _mean(float(value) for value in appropriate),
        "unexpected_tool_rate": _mean(
            float(value) for record in records
            if isinstance((value := (record.get("automatic_metrics") or {}).get("unexpected_tool_rate")), (int, float))
        ),
        "tool_success_rate": (
            sum(call.get("status") in {"ok", "supplied"} for call in calls) / len(calls) if calls else None
        ),
        "evidence_retrieval_rate": (
            sum(bool(call.get("evidence_nonempty")) for call in calls) / len(calls) if calls else None
        ),
        "external_service_failure_rate": (
            sum(call.get("status") == "error" for call in calls) / len(calls) if calls else None
        ),
        "grounding_score": _mean(grounding),
        "average_latency_seconds": _mean(elapsed),
        "p50_latency_seconds": _percentile(elapsed, 0.50),
        "p95_latency_seconds": _percentile(elapsed, 0.95),
        "total_llm_calls": sum(int((record.get("llm") or {}).get("calls") or 0) for record in records),
        "total_pubmed_calls": sum(call.get("tool") == "pubmed_tool" for call in calls),
        "total_enrichr_calls": sum(call.get("tool") == "pathway_tool" for call in calls),
        "total_ncbi_gene_calls": sum(call.get("tool") == "gene_annotation_tool" for call in calls),
    }


def aggregate_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the automatic aggregate metrics used by all report formats."""

    return _aggregate(records)


def _metric_row(scope: str, category: str, metric: str, value: Any) -> dict[str, Any]:
    units = {
        "total_cases": "cases", "successful_runs": "runs",
        "total_llm_calls": "calls", "total_pubmed_calls": "calls",
        "total_enrichr_calls": "calls", "total_ncbi_gene_calls": "calls",
    }
    if metric.endswith("latency_seconds"):
        unit = "seconds"
    elif metric.endswith("rate") or metric in {"router_accuracy", "expected_tool_recall", "grounding_score"}:
        unit = "rate"
    else:
        unit = units.get(metric, "count")
    if metric in {"total_cases", "successful_runs", "runtime_error_rate", "average_latency_seconds", "p50_latency_seconds", "p95_latency_seconds"}:
        level = "level_1_infrastructure"
    elif metric in {"router_accuracy", "expected_tool_recall", "appropriate_tool_call_rate", "unexpected_tool_rate", "tool_success_rate", "evidence_retrieval_rate", "external_service_failure_rate"}:
        level = "level_2_agent_behavior"
    elif metric == "grounding_score":
        level = "level_3_answer_quality"
    else:
        level = "cost_awareness"
    return {
        "scope": scope,
        "category": category,
        "level": level,
        "metric": metric,
        "value": value if value is not None else "",
        "unit": unit,
        "availability": "automatic" if value is not None else "N/A",
    }


def generate_summary(output_dir: Path, records: list[dict[str, Any]]) -> None:
    """Generate a synthetic-benchmark report and category-level metric CSV."""

    overall = _aggregate(records)
    grouped = {
        category: [record for record in records if record.get("category") == category]
        for category in sorted({str(record.get("category") or "uncategorized") for record in records})
    }
    category_stats = {category: _aggregate(items) for category, items in grouped.items()}
    providers = sorted({str(record.get("provider") or "") for record in records if record.get("provider")})
    models = sorted({str(record.get("model") or "default") for record in records})
    roi_count = len({record.get("roi_id") for record in records})
    failed = [record for record in records if record.get("errors")]
    tool_failed = [record for record in records if record.get("tool_errors")]

    lines = [
        "# Spatial Omics Copilot Evaluation Summary", "",
        "> This is a lightweight synthetic agent-workflow benchmark. It is not a clinical benchmark or a validated biological benchmark.", "",
        "## Evaluation configuration", "",
        f"- Provider: {', '.join(providers) or 'N/A'}",
        f"- Model: {', '.join(models) or 'N/A'}",
        f"- Synthetic ROIs: {roi_count}",
        f"- Cases: {len(records)}",
        f"- Categories: {', '.join(grouped) or 'N/A'}",
        "", "## Slide-ready results", "",
        "| Category | Cases | Successful | Router accuracy | Expected-tool recall | Tool success | Evidence retrieval | Grounding | Avg latency (s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    rows_for_table = [("Overall", overall), *[(name, category_stats[name]) for name in grouped]]
    for name, stats in rows_for_table:
        lines.append(
            f"| {name} | {stats['total_cases']} | {stats['successful_runs']} | "
            f"{_fmt(stats['router_accuracy'])} | {_fmt(stats['expected_tool_recall'])} | "
            f"{_fmt(stats['tool_success_rate'])} | {_fmt(stats['evidence_retrieval_rate'])} | "
            f"{_fmt(stats['grounding_score'])} | {_fmt(stats['average_latency_seconds'])} |"
        )

    lines.extend([
        "", "## Level 1 — Infrastructure", "",
        f"- Successful runs: {overall['successful_runs']}/{overall['total_cases']}",
        f"- Runtime error rate: {_fmt(overall['runtime_error_rate'])}",
        f"- Latency (average / p50 / p95): {_fmt(overall['average_latency_seconds'])} / {_fmt(overall['p50_latency_seconds'])} / {_fmt(overall['p95_latency_seconds'])} seconds",
        f"- External-service failure rate: {_fmt(overall['external_service_failure_rate'])} (reported separately from runtime failures)",
        "", "## Level 2 — Agent behavior", "",
        f"- Router accuracy: {_fmt(overall['router_accuracy'])}",
        f"- Expected-tool recall: {_fmt(overall['expected_tool_recall'])}",
        f"- Appropriate exact tool-set rate: {_fmt(overall['appropriate_tool_call_rate'])}",
        f"- Unexpected-tool rate: {_fmt(overall['unexpected_tool_rate'])}",
        f"- Tool success rate: {_fmt(overall['tool_success_rate'])}",
        f"- Evidence retrieval rate: {_fmt(overall['evidence_retrieval_rate'])}",
        "", "## Level 3 — Answer quality", "",
        f"- Automatic evidence-grounding score: {_fmt(overall['grounding_score'])}",
        "- Biological correctness, completeness, hallucination severity, and usefulness require human review in `human_review.csv`.",
        "", "## External calls and usage", "",
        f"- LLM calls: {overall['total_llm_calls']}",
        f"- PubMed calls: {overall['total_pubmed_calls']}",
        f"- Enrichr calls: {overall['total_enrichr_calls']}",
        f"- NCBI Gene calls: {overall['total_ncbi_gene_calls']}",
        "- Per-tool latency and token usage are N/A unless exposed by the production provider/tool contract; no dollar cost is inferred.",
        "", "## Failures and review queue", "",
        f"- Runtime/synthesis failures: {len(failed)}",
        f"- Runs with one or more external tool failures: {len(tool_failed)}",
    ])
    if failed:
        lines.append("  - Runtime/synthesis failure details:")
        for record in failed:
            lines.append(f"    - `{record.get('case_id')}`: {'; '.join(record.get('errors') or [])}")
    else:
        lines.append("  - Runtime/synthesis failure details: none")
    if tool_failed:
        lines.append("  - Tool failure details:")
        for record in tool_failed:
            details = "; ".join(
                f"{item.get('tool', 'tool')}: {item.get('error', 'unknown failure')}"
                for item in (record.get("tool_errors") or [])
            )
            lines.append(f"    - `{record.get('case_id')}`: {details}")
    else:
        lines.append("  - Tool failure details: none")
    lines.append(f"- Cases requiring scientific human review: {len(records)} (all generated answers should be reviewed)")

    lines.extend([
        "", "## Grounding calculation", "",
        "`evidence_grounding_score` is the unweighted mean of applicable transparent checks: (1) whether the answer refers to retrieved/ROI evidence, (2) precision of gene-like symbols against ROI/annotation/pathway-overlap genes, (3) whether numeric citations and PMID links map to retrieved PubMed records—including a zero when papers were retrieved but never referenced—and (4) explicit insufficient-evidence acknowledgement for designated negative cases. Missing components are excluded; an empty answer scores zero. This checks provenance, not biological correctness.",
        "", "## Human-review rubric", "",
        "Use 1–5 for biological correctness, evidence grounding, completeness, and usefulness (1 = unusable/unsupported/incomplete; 5 = accurate, fully grounded, complete, and useful). For `hallucination_1_5`, 1 = severe hallucination and 5 = no meaningful hallucination. Scores are never pre-filled.",
        "", "## Limitations", "",
        "- All checked-in gene lists are synthetic and intended for reproducible workflow evaluation.",
        "- Enrichr and NCBI results can change over time; retrieval metrics measure the observed run.",
        "- Gene-symbol heuristics can flag biological acronyms for review and cannot prove claim entailment.",
        "- Scientific correctness and real workflow/time savings require expert and user studies.",
    ])
    (output_dir / "evaluation_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    metric_names = list(overall)
    metric_rows = [_metric_row("overall", "all", metric, overall[metric]) for metric in metric_names]
    for category, stats in category_stats.items():
        metric_rows.extend(_metric_row("category", category, metric, stats[metric]) for metric in metric_names)

    human_fields = [
        "biological_correctness_1_5", "evidence_grounding_1_5", "completeness_1_5",
        "hallucination_1_5", "usefulness_1_5",
    ]
    business_fields = [
        "estimated_manual_minutes", "estimated_copilot_minutes", "workflow_steps_saved",
        "would_researcher_trust_answer_1_5", "would_researcher_use_for_followup_1_5",
    ]
    human = _read_scores(output_dir / "human_review.csv", human_fields)
    business = _read_scores(output_dir / "business_metrics_review.csv", business_fields)
    for field in human_fields:
        value = _mean(human[field])
        metric_rows.append({
            "scope": "overall", "category": "all", "level": "level_3_answer_quality",
            "metric": field, "value": value if value is not None else "", "unit": "1-5",
            "availability": "human-scored" if value is not None else "N/A - requires human review",
        })
    for field in business_fields:
        value = _mean(business[field])
        metric_rows.append({
            "scope": "overall", "category": "all", "level": "business_workflow",
            "metric": field, "value": value if value is not None else "",
            "unit": "minutes" if field.endswith("minutes") else ("steps" if field == "workflow_steps_saved" else "1-5"),
            "availability": "human-scored" if value is not None else "N/A - requires human review",
        })
    _write_csv(
        output_dir / "metrics_summary.csv",
        ["scope", "category", "level", "metric", "value", "unit", "availability"],
        metric_rows,
    )


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


__all__ = [
    "BUSINESS_FIELDS", "HUMAN_FIELDS", "ensure_review_templates",
    "generate_summary", "write_jsonl",
]
