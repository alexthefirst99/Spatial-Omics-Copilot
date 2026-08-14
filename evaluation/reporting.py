"""Write the five auditable output artifacts required by the proposal."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def _technical_rows(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"metric": "PubMed retrieval relevance", "result": metrics["technical"]["pubmed_retrieval_relevance"]["display"]},
        {"metric": "Pathway relevance", "result": metrics["technical"]["pathway_relevance"]["display"]},
        {"metric": "Image-to-gene connection", "result": metrics["technical"]["image_to_gene_connection"]["display"]},
        {"metric": "Groundedness", "result": metrics["technical"]["groundedness"]["display"]},
        {"metric": "Hallucination rate", "result": metrics["technical"]["hallucination_rate"]["display"]},
        {"metric": "Answer quality", "result": metrics["technical"]["answer_quality"]["display"]},
        {"metric": "Response time", "result": metrics["technical"]["response_time"]["display"]},
    ]


def _business_rows(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"metric": "Time saved", "result": metrics["business"]["time_saved"]["display"]},
        {"metric": "Workflow efficiency", "result": metrics["business"]["workflow_efficiency"]["display"]},
        {"metric": "Hypothesis usefulness", "result": metrics["business"]["hypothesis_usefulness"]["display"]},
        {"metric": "Decision quality", "result": metrics["business"]["decision_quality"]["display"]},
        {"metric": "Trust and adoption", "result": metrics["business"]["trust_and_adoption"]["display"]},
    ]


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in fields} for row in rows)


def write_outputs(
    output_dir: Path, records: list[dict[str, Any]], metrics: dict[str, Any],
    run_metadata: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "raw_results.json").write_text(json.dumps({
        "schema_version": "3.0", "run_metadata": run_metadata,
        "aggregate_metrics": metrics, "per_roi_results": records,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    technical = _technical_rows(metrics)
    business = _business_rows(metrics)
    _write_csv(output_dir / "technical_metrics.csv", ["metric", "result"], technical)
    _write_csv(output_dir / "business_metrics.csv", ["metric", "result"], business)

    per_roi: list[dict[str, Any]] = []
    for record in records:
        judgments = record.get("judgments") or {}
        text = judgments.get("text") or {}
        claims = text.get("claims") or []
        pubmed = text.get("pubmed") or []
        pathways = text.get("pathways") or []
        scores = text.get("scores") or {}
        mentioned = text.get("mentioned_genes") or []
        unsupported_genes = text.get("unsupported_mentioned_genes") or []
        elapsed = (record.get("timing") or {}).get("copilot_end_to_end_seconds")
        per_roi.append({
            "roi_id": record.get("roi_id"),
            "center_x": (record.get("roi") or {}).get("center", [None, None])[0],
            "center_y": (record.get("roi") or {}).get("center", [None, None])[1],
            "bounds": json.dumps((record.get("roi") or {}).get("bounds")),
            "spot_count": (record.get("roi") or {}).get("spot_count"),
            "crop_path": (record.get("roi") or {}).get("crop_path"),
            "pubmed_relevant": sum(item.get("label") == "relevant" for item in pubmed),
            "pubmed_evaluated": len(pubmed),
            "pathways_relevant": sum(item.get("label") == "relevant" for item in pathways),
            "pathways_evaluated": len(pathways),
            "image_to_gene": (judgments.get("vision") or {}).get("verdict", ""),
            "supported_claims": sum(item.get("label") == "supported" for item in claims),
            "verifiable_claims": len(claims),
            "unsupported_claims": sum(item.get("label") == "unsupported" for item in claims),
            "unsupported_genes": len(unsupported_genes),
            "mentioned_genes": len(mentioned),
            "biological_reasonableness": scores.get("biological_reasonableness", ""),
            "roi_specificity": scores.get("roi_specificity", ""),
            "clarity_understandability": scores.get("clarity_understandability", ""),
            "hypothesis_usefulness": scores.get("hypothesis_usefulness", ""),
            "decision_quality": scores.get("decision_quality", ""),
            "trust": scores.get("trust", ""),
            "adoption": scores.get("adoption", ""),
            "copilot_seconds": elapsed,
            "time_saved_seconds": 14400 - elapsed if isinstance(elapsed, (int, float)) else "",
            "time_saved_percent": (14400 - elapsed) / 144 if isinstance(elapsed, (int, float)) else "",
            "connected_stage_count": (record.get("workflow_efficiency") or {}).get("automatically_connected_stage_count"),
            "tool_call_count": (record.get("agent") or {}).get("tool_call_count"),
            "manual_data_reentry_steps": (record.get("workflow_efficiency") or {}).get("manual_data_reentry_steps"),
            "trace_valid": (record.get("agent_trace_validation") or {}).get("valid"),
            "status": (record.get("agent") or {}).get("status"),
            "errors": "; ".join(record.get("errors") or []),
            "judge_errors": "; ".join(judgments.get("errors") or []),
        })
    fields = list(per_roi[0]) if per_roi else ["roi_id", "status", "errors"]
    _write_csv(output_dir / "per_roi_results.csv", fields, per_roi)

    lines = [
        "# Spatial Omics Copilot Evaluation Summary", "",
        f"Evaluated {len(records)}/{run_metadata.get('expected_roi_count', 10)} deterministic real ROIs using seed 42. "
        "Unavailable measurements are reported as N/A; no missing result is fabricated.", "",
        "## Technical Metrics", "",
        "| Technical Metric | Result |", "| --- | --- |",
    ]
    lines.extend(f"| {row['metric']} | {row['result']} |" for row in technical)
    lines.extend(["", "## Business Metrics", "", "| Business Metric | Result |", "| --- | --- |"])
    lines.extend(f"| {row['metric']} | {row['result']} |" for row in business)
    lines.extend([
        "", "## Audit notes", "",
        f"- Dataset: `{(run_metadata.get('dataset') or {}).get('h5ad_path', 'N/A')}`",
        f"- H&E image: `{(run_metadata.get('dataset') or {}).get('image_path', 'N/A')}`",
        f"- Spatial coordinate frame: `{(run_metadata.get('dataset') or {}).get('coordinate_source', 'N/A')}`",
        f"- Provider/model: `{run_metadata.get('provider', 'N/A')}:{run_metadata.get('model', 'N/A')}`",
        f"- Validated agent traces: {sum(bool((r.get('agent_trace_validation') or {}).get('valid')) for r in records)}/{len(records)}",
        "- Full ROI bounds, spot counts, crops, retrieved evidence, judgments, raw judge output, timings, errors, and traces are in `raw_results.json`.",
    ])
    (output_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


__all__ = ["write_outputs"]
