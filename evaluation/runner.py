"""Run the existing ROI-agent-inference path without the browser UI."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evaluation.metrics import compute_automatic_metrics, summarize_tool_calls
from evaluation.report import ensure_review_templates, generate_summary, write_jsonl


AgentRunner = Callable[..., Any]
ModelRunner = Callable[[list[dict[str, Any]], str, str | None], str]


def _resolve_path(value: str | None, base: Path) -> Path | None:
    if not value:
        return None
    path = Path(os.path.expanduser(value))
    return path if path.is_absolute() else (base / path).resolve()


def _load_source(case: dict[str, Any], config_dir: Path) -> tuple[list[dict], dict, dict, list[str]]:
    """Load ROI genes/image metadata from inline, context, workspace, or h5ad input."""

    source = case.get("source") or {}
    source_type = source.get("type", "inline")
    errors: list[str] = []
    roi = {"roi_id": case.get("roi_id", ""), "selection_type": source.get("selection_type", "polygon")}
    image = {"crop_path": "", "status_message": "No ROI image configured."}

    if source_type == "inline":
        genes = list(source.get("gene_objects") or [])
    elif source_type == "context_file":
        path = _resolve_path(source.get("path"), config_dir)
        if path is None or not path.exists():
            return [], roi, image, [f"ROI context file not found: {path}"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        genes = list(payload.get("gene_objects") or payload.get("top_genes") or [])
    elif source_type == "workspace":
        work_dir = _resolve_path(source.get("work_dir"), config_dir)
        user_dir = work_dir / source.get("user_dir", "user") if work_dir else None
        candidates = [user_dir / "roi_context.json", user_dir / "cluster_context.json"] if user_dir else []
        existing = [path for path in candidates if path.exists()]
        if not existing:
            return [], roi, image, [f"No cached ROI/cluster context found under {user_dir}"]
        context_path = max(existing, key=lambda path: path.stat().st_mtime)
        payload = json.loads(context_path.read_text(encoding="utf-8"))
        genes = list(payload.get("gene_objects") or [])
        if "cluster_id" in payload:
            roi["selection_type"] = "cluster"
            roi["cluster_id"] = str(payload["cluster_id"])
        crop_path = user_dir / "roi_crop.png"
        if crop_path.exists():
            image = {"crop_path": str(crop_path), "status_message": ""}
    elif source_type == "h5ad_roi":
        h5ad_path = _resolve_path(source.get("h5ad_path"), config_dir)
        if h5ad_path is None or not h5ad_path.exists():
            return [], roi, image, [f"h5ad file not found: {h5ad_path}"]
        from rag.deg import run_roi_deg

        selection = source.get("polygon_points") or source.get("selection_mask")
        deg_result = run_roi_deg(str(h5ad_path), selection, source.get("deg_config"))
        genes = list(deg_result.to_dict().get("top_genes") or [])
        roi["polygon_points"] = source.get("polygon_points")
    else:
        return [], roi, image, [f"Unsupported source type: {source_type}"]

    image_path = _resolve_path(source.get("roi_image_path"), config_dir)
    if image_path:
        if image_path.exists():
            image = {"crop_path": str(image_path), "status_message": ""}
        else:
            errors.append(f"ROI image not found: {image_path}")
    return genes, roi, image, errors


def _default_agent_runner(**kwargs: Any) -> Any:
    from rag.copilot_agent import run_copilot_agent

    return run_copilot_agent(**kwargs)


def _default_model_runner(messages: list[dict[str, Any]], provider: str, model: str | None) -> str:
    from app.inference import run_model_inference

    return "".join(run_model_inference(messages, provider=provider, model_name=model)).strip()


def _generation_error(answer: str) -> str:
    lower = (answer or "").lower()
    markers = (
        "ollama is not reachable", "error querying ollama", "error during generation",
        "deepinfra is not configured", "deepinfra is disabled", "deepinfra call failed",
        "unsupported model provider", "ollama python client is not installed",
    )
    return answer if any(marker in lower for marker in markers) else ""


def _tool_result(result: Any, name: str) -> dict[str, Any]:
    outcome = (getattr(result, "tool_outcomes", {}) or {}).get(name) or {}
    payload = outcome.get("result")
    return payload if isinstance(payload, dict) else {}


def execute_query(
    case: dict[str, Any], query_case: dict[str, Any], *, config_dir: Path,
    agent_runner: AgentRunner = _default_agent_runner,
    model_runner: ModelRunner = _default_model_runner,
    provider: str = "ollama", model: str | None = None,
    generation_enabled: bool = True,
) -> dict[str, Any]:
    """Execute one query and return the complete raw evaluation record."""

    genes, roi, roi_image, source_errors = _load_source(case, config_dir)
    query = str(query_case.get("query") or "").strip()
    started = time.perf_counter()
    # ``errors`` is reserved for run-level failures. Tool errors are recorded
    # separately so one unavailable optional service does not automatically
    # invalidate an otherwise useful scientific answer.
    errors = list(source_errors)
    result = None
    answer = ""
    trace: list[dict[str, Any]] = []
    llm_status = "not_called"
    llm_calls = 0

    gene_symbols = [
        str(row.get("gene") or row.get("gene_symbol") or row.get("symbol") or "").strip().upper()
        for row in genes if isinstance(row, dict)
    ]
    gene_symbols = [gene for gene in gene_symbols if gene]
    tools_requested: list[str] = []
    try:
        # This is the production deterministic planner, called read-only for
        # observability before the graph applies supplied-result/budget rules.
        from rag.copilot_agent.routing import plan_tools

        tools_requested = list(plan_tools(
            query,
            genes=gene_symbols,
            has_genes=bool(gene_symbols),
            has_roi_image=bool(roi_image.get("crop_path")),
        ).tools)
    except Exception:
        # The actual agent invocation below remains authoritative. A planner
        # observability failure should not prevent the benchmark from running.
        tools_requested = []

    try:
        image_available = bool(roi_image.get("crop_path"))
        image_attached = False
        if image_available:
            from rag.copilot_agent.multimodal import model_supports_vision
            image_attached = model_supports_vision(model, {"deepinfra": {"model": model or ""}})

        result = agent_runner(
            question=query,
            roi=roi,
            roi_image=roi_image,
            deg=genes,
            config=query_case.get("agent_config") or case.get("agent_config") or {},
            label=case.get("label") or case.get("roi_id") or "ROI",
            image_attached=image_attached,
            max_tool_calls=int(case.get("max_tool_calls", 5)),
            semantic_rerank=bool(case.get("semantic_rerank", False)),
            disease=case.get("disease"),
        )
        trace = [step.to_dict() if hasattr(step, "to_dict") else dict(step) for step in (getattr(result, "trace", []) or [])]
        if generation_enabled:
            llm_calls = 1
            prompt = query + "\n\nRespond in 1-2 concise sentences. Be direct." + (getattr(result, "context_str", "") or "")
            message: dict[str, Any] = {"role": "user", "content": prompt}
            if image_attached:
                message["images"] = [roi_image["crop_path"]]
            answer = model_runner([message], provider, model).strip()
            generation_error = _generation_error(answer)
            if generation_error:
                errors.append(generation_error)
                # Provider status text is an error artifact, not a scientific
                # final answer. Preserve it in errors and keep final_answer empty.
                answer = ""
                synthesis_status = "error"
                synthesis_step = "Final response synthesis failed"
                llm_status = "error"
            elif not answer:
                errors.append("Model returned an empty final answer.")
                synthesis_status = "error"
                synthesis_step = "Final response synthesis failed"
                llm_status = "error"
            else:
                synthesis_status = "ok"
                synthesis_step = "Synthesized final response"
                llm_status = "ok"
        else:
            synthesis_status = "skipped"
            synthesis_step = "Final response synthesis skipped"
            llm_status = "skipped"

        trace.append({
            "step": synthesis_step, "detail": f"{provider}:{model or 'default'}",
            "icon": "agent", "tool": provider, "status": synthesis_status,
            "input_summary": "question plus observable ROI/tool evidence",
            "output_summary": (
                f"{len(answer)} characters" if answer else
                (errors[-1] if errors else "generation disabled")
            ),
        })
    except Exception as exc:  # Keep later cases runnable and preserve exact blocker.
        errors.append(f"{type(exc).__name__}: {exc}")

    elapsed = time.perf_counter() - started
    tool_outcomes = getattr(result, "tool_outcomes", {}) if result is not None else {}
    tools_called = list(getattr(result, "tools_called", []) or []) if result is not None else []
    tool_calls = summarize_tool_calls(tools_called, tool_outcomes, trace)
    tool_errors = [
        {"tool": call["tool"], "error": call["error"] or call["output_summary"]}
        for call in tool_calls if call.get("failure")
    ]
    actual_route = getattr(result, "intent", "") if result is not None else ""
    expected_route = str(
        query_case.get("expected_route")
        or query_case.get("expected_intent")
        or case.get("expected_route")
        or ""
    )
    expected_tools = list(query_case.get("expected_tools") or case.get("expected_tools") or [])
    category = str(query_case.get("category") or case.get("category") or "uncategorized")
    case_id = str(query_case.get("case_id") or query_case.get("query_id") or "")
    expect_insufficient = bool(
        query_case.get("expect_insufficient_evidence", case.get("expect_insufficient_evidence", False))
    )
    pubmed_results = _tool_result(result, "pubmed_tool") if result is not None else {}
    pathway_results = _tool_result(result, "pathway_tool") if result is not None else {}
    annotation_results = _tool_result(result, "gene_annotation_tool") if result is not None else {}
    record: dict[str, Any] = {
        "schema_version": "2.0",
        "run_id": f"{case.get('roi_id', 'roi')}::{query_case.get('query_id', uuid.uuid4().hex[:8])}",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "case_id": case_id,
        "category": category,
        "case_kind": case.get("case_kind", "unspecified"),
        "roi_id": case.get("roi_id", ""),
        "roi_label": case.get("label", ""),
        "query_id": query_case.get("query_id", ""),
        "query": query,
        "question": query,
        "disease": case.get("disease", ""),
        "expect_insufficient_evidence": expect_insufficient,
        "expected_route": expected_route,
        "actual_route": actual_route,
        "expected_intent": expected_route,
        "detected_route_intent": actual_route,
        "expected_tools": expected_tools,
        "tools_requested": tools_requested,
        "tools_actually_called": tools_called,
        "tools_called": tools_called,
        "tool_calls": tool_calls,
        "tool_results_summary": [
            {
                key: call.get(key)
                for key in ("tool", "service", "status", "result_count", "evidence_nonempty", "error")
            }
            for call in tool_calls
        ],
        "tool_outcomes": tool_outcomes or {},
        "tool_errors": tool_errors,
        "structured_agent_trace": trace,
        "roi": {
            **roi,
            "label": case.get("label", ""),
            "gene_objects": genes,
        },
        "retrieve_input_evidence": {
            "source": case.get("source", {}), "resolved_roi": roi,
            "gene_count": len(genes), "image_path": roi_image.get("crop_path", ""),
        },
        "pubmed_results": pubmed_results,
        "pathway_results": pathway_results,
        "gene_image_evidence": {
            "gene_evidence": genes,
            "gene_annotations": annotation_results,
            "image_available": bool(roi_image.get("crop_path")),
            "image_attached_to_model": bool(getattr(result, "used_roi_image", False)) if result is not None else False,
            "roi_image": roi_image,
        },
        "evidence": {
            "gene_annotations": annotation_results,
            "pathways": pathway_results,
            "pubmed": pubmed_results,
            "context_nonempty": bool(getattr(result, "context_str", "") if result is not None else ""),
        },
        "evidence_context": getattr(result, "context_str", "") if result is not None else "",
        "final_answer": answer,
        "answer": answer,
        "provider": provider,
        "model": model or "",
        "llm": {
            "status": llm_status,
            "calls": llm_calls,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        },
        "total_response_time_seconds": round(elapsed, 6),
        "errors": list(dict.fromkeys(str(error) for error in errors if error)),
    }
    record["run_status"] = (
        "error" if record["errors"] else
        "partial" if tool_errors or llm_status == "skipped" else
        "completed"
    )
    record["automatic_metrics"] = compute_automatic_metrics(record)
    record["human_evaluation"] = {
        "biological_correctness_1_5": None,
        "evidence_grounding_1_5": None,
        "completeness_1_5": None,
        "hallucination_1_5": None,
        "usefulness_1_5": None,
        "reviewer_notes": "",
    }
    return record


def write_workflow_document(output_dir: Path) -> None:
    """Export the implemented UI and LangGraph paths discovered in the repo."""

    text = """# Implemented Agentic RAG Workflow

This diagram documents the code as implemented; it does not add evaluation-only agent nodes.

```mermaid
flowchart TD
    A[Whole-slide image and spatial h5ad] --> B[User selects polygon ROI or cluster]
    B --> C[DEG computed immediately in app/app.py]
    B --> D[Polygon ROI crop cached when available]
    C --> E[roi_context.json or cluster_context.json]
    E --> F[Chat query passed to run_copilot_agent]
    F --> G[Deterministic route / intent classification]
    G --> H{Pending evidence tools?}
    H -->|Gene question| I[NCBI Gene annotation]
    H -->|Pathway question| J[GO / KEGG Enrichr pathway tool]
    H -->|Literature question| K[NCBI PubMed ESearch + EFetch]
    H -->|Image or general chat| L[No external evidence tool]
    I --> M{More queued tools and budget remains?}
    J --> M
    K --> M
    M -->|Yes| H
    M -->|No; maximum 5 calls| N[Synthesize observable evidence context]
    L --> N
    D --> O[Worker attaches crop only for a vision-capable model]
    N --> P[app/worker.py appends evidence to user prompt]
    O --> P
    P --> Q[Ollama or optional DeepInfra final response]
```

## Actual control flow

1. ROI input is not an agent tool. `app/app.py` calls `get_roi_high_expression_genes` or `get_cluster_high_expression_genes` as soon as a selection changes, then caches the DEG rows. A polygon crop is also cached when a slide is available.
2. `app/routes.py` chooses the most recently updated ROI or cluster context and calls `run_copilot_agent(question, deg, label, disease)` directly. This is the normal programmatic query entry point below the browser UI.
3. `copilot_agent/routing.py` deterministically classifies the query. Specific gene, pathway, and literature requests select their matching tools; a broad biological interpretation selects all three; visual and general-chat intents select no external tools.
4. The LangGraph graph is `START -> route -> run_tool (loop) -> synthesize -> END`. Tool ordering is gene annotation, pathway, PubMed, and pathway names are passed into PubMed when both branches run. The hard per-turn budget is five tool calls. A plain-Python fallback executes the same nodes when LangGraph is unavailable.
5. The graph synthesis node formats ROI/DEG, annotations, pathways, PubMed abstracts, image availability, and evidence gaps into `context_str`. It does not generate hidden reasoning.
6. `app/worker.py` appends `context_str` to the prompt, attaches the ROI crop only when the selected model supports images, and streams the final response from local Ollama or optional DeepInfra.

The standalone evaluator follows the same cached-evidence -> agent -> inference boundary synchronously and records a final `Synthesized final response` event after observable generation completes. It never records private chain-of-thought.
"""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "agentic_rag_workflow.md").write_text(text, encoding="utf-8")


def load_cases(config_path: Path) -> dict[str, Any]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("cases"), list) or not payload["cases"]:
        raise ValueError("Evaluation config must contain a non-empty 'cases' list.")

    from rag.copilot_agent.routing import (
        ALL_TOOLS,
        INTENT_EXPLANATION,
        INTENT_GENERAL,
        INTENT_GENE_FUNCTION,
        INTENT_IMAGE,
        INTENT_LITERATURE,
        INTENT_PATHWAY,
        INTENT_SUMMARY,
    )

    valid_routes = {
        INTENT_EXPLANATION, INTENT_GENERAL, INTENT_GENE_FUNCTION, INTENT_IMAGE,
        INTENT_LITERATURE, INTENT_PATHWAY, INTENT_SUMMARY,
    }
    valid_categories = {
        "gene_lookup", "pathway_enrichment", "literature_retrieval",
        "multi_step_interpretation", "negative_failure_handling", "uncategorized",
    }
    seen_ids: set[str] = set()
    for case_index, case in enumerate(payload["cases"], start=1):
        if not isinstance(case, dict):
            raise ValueError(f"Case {case_index} must be an object.")
        queries = case.get("queries") or []
        if not isinstance(queries, list) or not queries:
            raise ValueError(f"Case {case.get('roi_id', case_index)!r} must contain queries.")
        for query_index, query in enumerate(queries, start=1):
            if isinstance(query, str):
                continue  # Backward-compatible shorthand.
            if not isinstance(query, dict) or not str(query.get("query") or "").strip():
                raise ValueError(f"Query {query_index} in case {case_index} must contain non-empty 'query'.")
            query_id = str(query.get("case_id") or query.get("query_id") or "").strip()
            if query_id:
                if query_id in seen_ids:
                    raise ValueError(f"Duplicate evaluation case/query id: {query_id}")
                seen_ids.add(query_id)
            category = str(query.get("category") or case.get("category") or "uncategorized")
            if category not in valid_categories:
                raise ValueError(f"Unsupported evaluation category {category!r} for {query_id or query_index}.")
            route = str(query.get("expected_route") or query.get("expected_intent") or "")
            if route and route not in valid_routes:
                raise ValueError(f"Unknown production route {route!r} for {query_id or query_index}.")
            expected_tools = query.get("expected_tools") or case.get("expected_tools") or []
            if not isinstance(expected_tools, list) or any(tool not in ALL_TOOLS for tool in expected_tools):
                raise ValueError(f"Unknown production tool in expected_tools for {query_id or query_index}.")
    return payload


def run_evaluation(
    config_path: str | Path, output_dir: str | Path, *,
    provider: str | None = None, model: str | None = None,
    generation_enabled: bool = True,
    agent_runner: AgentRunner = _default_agent_runner,
    model_runner: ModelRunner = _default_model_runner,
) -> list[dict[str, Any]]:
    config_path = Path(config_path).resolve()
    output_dir = Path(output_dir).resolve()
    config = load_cases(config_path)
    generation = config.get("generation") or {}
    provider = provider or generation.get("provider") or "ollama"
    model = model or generation.get("model")

    records = []
    for case in config["cases"]:
        for query in case.get("queries") or []:
            query_case = {"query_id": uuid.uuid4().hex[:8], "query": query} if isinstance(query, str) else dict(query)
            records.append(execute_query(
                case, query_case, config_dir=config_path.parent,
                agent_runner=agent_runner, model_runner=model_runner,
                provider=provider, model=model, generation_enabled=generation_enabled,
            ))

    write_jsonl(output_dir / "raw_results.jsonl", records)
    ensure_review_templates(output_dir, records)
    generate_summary(output_dir, records)
    write_workflow_document(output_dir)
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PROJECT_ROOT / "evaluation" / "eval_cases.json"))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "evaluation_outputs"))
    parser.add_argument("--provider", choices=("ollama", "deepinfra"))
    parser.add_argument("--model")
    parser.add_argument("--no-generation", action="store_true", help="Run routing/tools but record final synthesis as skipped.")
    args = parser.parse_args(argv)
    records = run_evaluation(
        args.config, args.output_dir, provider=args.provider, model=args.model,
        generation_enabled=not args.no_generation,
    )
    failed = sum(bool(record.get("errors")) for record in records)
    print(f"Evaluation complete: {len(records)} run(s), {failed} with error(s).")
    print(f"Outputs: {Path(args.output_dir).resolve()}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
