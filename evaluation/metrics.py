"""Transparent, evidence-based metrics for Spatial Omics Copilot runs.

The metrics in this module intentionally avoid treating lexical similarity as
scientific correctness.  They score observable agent behaviour (route, tools,
retrieval) and conservative grounding checks.  Expert biological correctness,
completeness, and usefulness remain human-review fields.
"""

from __future__ import annotations

import re
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

_GENE_TOKEN = re.compile(r"\b[A-Z][A-Z0-9-]{1,14}\b")
_NON_GENE_ACRONYMS = {
    "API", "DNA", "ECM", "GO", "H&E", "KEGG", "LLM", "NCBI", "N/A",
    "PMID", "PMIDS", "RNA", "ROI", "ROIS", "URL",
}
_INSUFFICIENT_PATTERNS = (
    "insufficient evidence", "not enough evidence", "no evidence", "no relevant",
    "no matching", "not found", "could not find", "cannot determine", "can't determine",
    "uncertain", "unclear", "unavailable", "tool failed", "weak enrichment",
    "no significant", "no enriched", "does not support", "do not support",
)


def _rows(payload: Any, key: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    values = payload.get(key)
    return [row for row in (values or []) if isinstance(row, dict)]


def _term_present(text: str, term: str) -> bool:
    term = str(term or "").strip()
    if not term:
        return False
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])", text, re.I) is not None


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def result_count(tool_name: str, outcome: Any) -> int:
    """Return the number of public evidence rows in a serialized tool outcome."""

    if not isinstance(outcome, dict):
        return 0
    payload = outcome.get("result")
    if not isinstance(payload, dict):
        return 0
    key = TOOL_RESULT_KEYS.get(tool_name)
    return len(payload.get(key) or []) if key else 0


def summarize_tool_calls(
    tools_called: Iterable[str],
    tool_outcomes: dict[str, Any] | None,
    trace: Iterable[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Build ordered, debuggable tool-call records from production metadata.

    The production graph currently does not expose per-tool timing, so
    ``latency_seconds`` is explicitly ``None``.  The field is ready for timing
    data if the production contract adds it later.
    """

    outcomes = tool_outcomes or {}
    trace_by_tool: dict[str, dict[str, Any]] = {}
    for step in trace:
        tool = str(step.get("tool") or "") if isinstance(step, dict) else ""
        if tool in TOOL_RESULT_KEYS:
            trace_by_tool[tool] = step

    ordered = _unique([*tools_called, *outcomes.keys()])
    summaries: list[dict[str, Any]] = []
    for order, name in enumerate(ordered, start=1):
        outcome = outcomes.get(name) if isinstance(outcomes.get(name), dict) else {}
        step = trace_by_tool.get(name, {})
        status = str(outcome.get("status") or step.get("status") or "unknown")
        count = result_count(name, outcome)
        summaries.append({
            "order": order,
            "tool": name,
            "service": TOOL_SERVICE_NAMES.get(name, name),
            "status": status,
            "success": status in {"ok", "supplied"},
            "failure": status == "error",
            "result_count": count,
            "evidence_nonempty": count > 0,
            "latency_seconds": outcome.get("latency_seconds"),
            "input_summary": str(outcome.get("input_summary") or step.get("input_summary") or ""),
            "output_summary": str(outcome.get("output_summary") or step.get("output_summary") or ""),
            "error": str(outcome.get("error") or ""),
        })
    return summaries


def compare_expected_tools(expected: Iterable[str], actual: Iterable[str]) -> dict[str, Any]:
    """Compare expected and actual tools without assigning scientific quality."""

    expected_list = _unique(expected)
    actual_list = _unique(actual)
    expected_set, actual_set = set(expected_list), set(actual_list)
    matched = expected_set & actual_set
    unexpected = actual_set - expected_set
    recall = len(matched) / len(expected_set) if expected_set else (1.0 if not actual_set else None)
    unexpected_rate = len(unexpected) / len(actual_set) if actual_set else 0.0
    appropriate = actual_set == expected_set
    return {
        "expected_tool_recall": recall,
        "unexpected_tool_rate": unexpected_rate,
        "appropriate_tool_call": appropriate,
        "missing_expected_tools": sorted(expected_set - actual_set),
        "unexpected_tools": sorted(unexpected),
    }


def _evidence_entities(record: dict[str, Any]) -> dict[str, Any]:
    gene_rows = (record.get("gene_image_evidence") or {}).get("gene_evidence") or []
    roi_genes = {
        str(row.get("gene") or "").strip().upper()
        for row in gene_rows if isinstance(row, dict) and row.get("gene")
    }

    annotations = _rows(record.get("gene_image_evidence", {}).get("gene_annotations"), "genes")
    annotation_genes = {
        str(row.get("gene_symbol") or row.get("symbol") or "").strip().upper()
        for row in annotations
    }
    pathways = _rows(record.get("pathway_results"), "pathways")
    overlap_genes = {
        str(gene).strip().upper()
        for row in pathways
        for gene in (row.get("overlap_genes") or row.get("overlap") or [])
    }
    pathway_names = [str(row.get("name") or "").strip() for row in pathways if row.get("name")]
    papers = _rows(record.get("pubmed_results"), "papers")
    pmids = {str(row.get("pmid") or "").strip() for row in papers if row.get("pmid")}
    titles = [str(row.get("title") or "").strip() for row in papers if row.get("title")]
    return {
        "supported_genes": roi_genes | annotation_genes | overlap_genes,
        "roi_genes": roi_genes,
        "pathway_names": pathway_names,
        "papers": papers,
        "pmids": pmids,
        "titles": titles,
    }


def _mentioned_pmids(answer: str) -> set[str]:
    direct = set(re.findall(r"\bPMID\s*[:#]?\s*(\d{5,10})\b", answer, re.I))
    links = set(re.findall(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{5,10})", answer, re.I))
    return direct | links


def compute_grounding_metrics(record: dict[str, Any]) -> dict[str, Any]:
    """Compute a conservative, interpretable evidence-grounding score.

    The score is the unweighted mean of applicable components:

    * ``evidence_reference``: answer mentions an ROI/retrieved gene, pathway,
      retrieved paper title (first eight words), or retrieved PMID.
    * ``gene_support``: fraction of gene-like uppercase symbols in the answer
      that occur in ROI, annotation, or pathway-overlap evidence.
    * ``citation_support``: all numeric ``[n]`` citations and PMID/link mentions
      map to papers retrieved in this run.  If PubMed evidence exists but the
      answer cites none, this component is 0.
    * ``insufficient_evidence_acknowledgement``: for cases marked
      ``expect_insufficient_evidence``, the answer explicitly qualifies the
      lack/weakness/unavailability of evidence.

    Components that do not apply are ``None`` and are excluded.  An empty
    answer scores 0.  This is provenance checking, not a biological-correctness
    score.
    """

    answer = str(record.get("final_answer") or "").strip()
    if not answer:
        return {
            "evidence_grounding_score": 0.0,
            "grounding_components": {
                "evidence_reference": 0.0,
                "gene_support": None,
                "citation_support": None,
                "insufficient_evidence_acknowledgement": None,
            },
            "mentioned_genes": [],
            "unsupported_genes": [],
            "mentioned_pmids": [],
            "unsupported_pmids": [],
            "insufficient_evidence_acknowledged": False,
        }

    entities = _evidence_entities(record)
    supported_genes: set[str] = entities["supported_genes"]
    tokens = {
        token for token in _GENE_TOKEN.findall(answer)
        if token not in _NON_GENE_ACRONYMS and not token.isdigit()
    }
    # Only score symbols when there is gene evidence or when the answer uses
    # gene-like tokens. This deliberately errs toward flagging for review.
    unsupported_genes = sorted(tokens - supported_genes)
    gene_support = len(tokens & supported_genes) / len(tokens) if tokens else None

    evidence_mentions = any(_term_present(answer, gene) for gene in supported_genes)
    evidence_mentions = evidence_mentions or any(
        name and name.casefold() in answer.casefold() for name in entities["pathway_names"]
    )
    for title in entities["titles"]:
        title_anchor = " ".join(title.split()[:8])
        evidence_mentions = evidence_mentions or (
            bool(title_anchor) and title_anchor.casefold() in answer.casefold()
        )
    mentioned_pmids = _mentioned_pmids(answer)
    evidence_mentions = evidence_mentions or bool(mentioned_pmids & entities["pmids"])

    numeric_citations = re.findall(r"\[(\d+)\]", answer)
    valid_indices = {str(index) for index in range(1, len(entities["papers"]) + 1)}
    citation_items = len(numeric_citations) + len(mentioned_pmids)
    citation_supported = sum(index in valid_indices for index in numeric_citations)
    citation_supported += sum(pmid in entities["pmids"] for pmid in mentioned_pmids)
    if citation_items:
        citation_support = citation_supported / citation_items
    elif entities["papers"]:
        citation_support = 0.0
    else:
        citation_support = None

    expect_insufficient = bool(record.get("expect_insufficient_evidence"))
    lower_answer = answer.casefold()
    acknowledged = any(pattern in lower_answer for pattern in _INSUFFICIENT_PATTERNS)
    negative_component = float(acknowledged) if expect_insufficient else None

    components = {
        "evidence_reference": float(evidence_mentions),
        "gene_support": gene_support,
        "citation_support": citation_support,
        "insufficient_evidence_acknowledgement": negative_component,
    }
    applicable = [float(value) for value in components.values() if isinstance(value, (int, float))]
    score = sum(applicable) / len(applicable) if applicable else 0.0
    return {
        "evidence_grounding_score": score,
        "grounding_components": components,
        "mentioned_genes": sorted(tokens),
        "unsupported_genes": unsupported_genes,
        "mentioned_pmids": sorted(mentioned_pmids),
        "unsupported_pmids": sorted(mentioned_pmids - entities["pmids"]),
        "insufficient_evidence_acknowledged": acknowledged if expect_insufficient else None,
    }


def compute_automatic_metrics(record: dict[str, Any]) -> dict[str, Any]:
    """Compute Level 1–3 metrics supported by observable run artifacts."""

    gene_rows = (record.get("gene_image_evidence") or {}).get("gene_evidence") or []
    genes = [str(row.get("gene", "")).strip() for row in gene_rows if isinstance(row, dict)]
    genes = [gene for gene in genes if gene]

    pubmed_rows = _rows(record.get("pubmed_results"), "papers")
    pubmed_rate = None
    if pubmed_rows:
        relevant = 0
        disease = str(record.get("disease") or "").strip()
        for paper in pubmed_rows:
            text = f"{paper.get('title', '')} {paper.get('abstract', '')}"
            if any(_term_present(text, gene) for gene in genes) or (
                disease and disease.casefold() in text.casefold()
            ):
                relevant += 1
        pubmed_rate = relevant / len(pubmed_rows)

    pathway_rows = _rows(record.get("pathway_results"), "pathways")
    pathway_overlap_rate = None
    if pathway_rows:
        gene_set = {gene.upper() for gene in genes}
        connected = 0
        for pathway in pathway_rows:
            overlap = pathway.get("overlap_genes", pathway.get("overlap", [])) or []
            if any(str(gene).upper() in gene_set for gene in overlap):
                connected += 1
        pathway_overlap_rate = connected / len(pathway_rows)

    expected_route = str(record.get("expected_route") or record.get("expected_intent") or "").strip()
    actual_route = str(record.get("actual_route") or record.get("detected_route_intent") or "").strip()
    route_match = actual_route == expected_route if expected_route else None

    expected_tools = record.get("expected_tools") or []
    actual_tools = record.get("tools_actually_called") or record.get("tools_called") or []
    tool_comparison = compare_expected_tools(expected_tools, actual_tools)
    calls = record.get("tool_calls") or []
    statuses = [call.get("status") for call in calls if isinstance(call, dict)]
    tool_success_rate = (
        sum(status in {"ok", "supplied"} for status in statuses) / len(statuses)
        if statuses else None
    )
    evidence_retrieval_rate = (
        sum(bool(call.get("evidence_nonempty")) for call in calls if isinstance(call, dict)) / len(calls)
        if calls else None
    )
    external_failure_rate = (
        sum(status == "error" for status in statuses) / len(statuses)
        if statuses else None
    )

    grounding = compute_grounding_metrics(record)
    evidence = record.get("gene_image_evidence") or {}
    return {
        "level_1_infrastructure": {
            "run_completed": not bool(record.get("errors")),
            "llm_status": record.get("llm", {}).get("status", "unknown"),
            "external_service_failure_rate": external_failure_rate,
        },
        "level_2_agent_behavior": {
            "expected_route_match": route_match,
            **tool_comparison,
            "tool_success_rate": tool_success_rate,
            "evidence_retrieval_rate": evidence_retrieval_rate,
        },
        "level_3_answer_quality": grounding,
        # Flat aliases keep downstream CSV/report consumption straightforward
        # and preserve the names emitted by schema version 1.0.
        "response_time_seconds": record.get("total_response_time_seconds"),
        "pubmed_lexical_relevance_rate": pubmed_rate,
        "pathway_input_gene_overlap_rate": pathway_overlap_rate,
        "image_gene_evidence_available": bool(evidence.get("image_available") and evidence.get("gene_evidence")),
        "expected_route_match": route_match,
        **tool_comparison,
        "tool_success_rate": tool_success_rate,
        "evidence_retrieval_rate": evidence_retrieval_rate,
        "external_service_failure_rate": external_failure_rate,
        **grounding,
    }


__all__ = [
    "TOOL_RESULT_KEYS",
    "TOOL_SERVICE_NAMES",
    "compare_expected_tools",
    "compute_automatic_metrics",
    "compute_grounding_metrics",
    "result_count",
    "summarize_tool_calls",
]
