"""Run the proposal's 7 technical and 5 business metrics on real ROIs."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for path in (PROJECT_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from evaluation.judges import JudgeClient, judge_roi
from evaluation.metrics import aggregate_proposal_metrics, summarize_tool_calls
from evaluation.reporting import write_outputs


AgentRunner = Callable[..., Any]
ModelRunner = Callable[[list[dict[str, Any]], str, str | None], str]

DEFAULT_QUESTION = (
    "Analyze this ROI's visible morphology and differential-expression genes. "
    "Identify a plausible biological state, relevant pathways, and supporting "
    "PubMed literature. Explicitly connect visible morphology to specific ROI "
    "genes, distinguish observation from inference, and cite retrieved papers "
    "by PMID."
)


def _resolve_path(value: str, base: Path) -> Path:
    path = Path(os.path.expanduser(value))
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "to_dict"):
        return _json_safe(value.to_dict())
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)


def load_cases(config_path: Path) -> dict[str, Any]:
    """Load and validate the real-dataset evaluation configuration."""

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    dataset = payload.get("dataset") or {}
    generation = payload.get("roi_generation") or {}
    for field in ("h5ad_path", "image_path"):
        if not str(dataset.get(field) or "").strip():
            raise ValueError(f"dataset.{field} is required.")
    count = int(generation.get("count", 10))
    if count != 10:
        raise ValueError("roi_generation.count must be 10 for the proposal evaluation.")
    if int(generation.get("roi_size_pixels", 256)) <= 0:
        raise ValueError("roi_generation.roi_size_pixels must be positive.")
    if int(generation.get("min_spots", 100)) <= 0:
        raise ValueError("roi_generation.min_spots must be positive.")
    return payload


def _dataset_fingerprint(h5ad_path: Path, image_path: Path) -> dict[str, Any]:
    return {
        "h5ad_path": str(h5ad_path),
        "h5ad_size": h5ad_path.stat().st_size,
        "h5ad_mtime_ns": h5ad_path.stat().st_mtime_ns,
        "image_path": str(image_path),
        "image_size": image_path.stat().st_size,
        "image_mtime_ns": image_path.stat().st_mtime_ns,
    }


def _square(center: np.ndarray, size: int) -> tuple[list[list[list[float]]], list[float]]:
    half = size / 2.0
    x, y = map(float, center)
    bounds = [x - half, y - half, x + half, y + half]
    ring = [
        [bounds[0], bounds[1]], [bounds[2], bounds[1]],
        [bounds[2], bounds[3]], [bounds[0], bounds[3]],
        [bounds[0], bounds[1]],
    ]
    return [ring], bounds


def _spot_mask(coordinates: np.ndarray, bounds: list[float]) -> np.ndarray:
    x0, y0, x1, y1 = bounds
    x, y = coordinates[:, 0], coordinates[:, 1]
    return (
        np.isfinite(x) & np.isfinite(y)
        & (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
    )


def generate_real_rois(
    config: dict[str, Any], config_path: Path, output_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Create/reuse ten deterministic square ROIs centered on real spots."""

    import anndata as ad
    from PIL import Image
    from rag.deg.coordinates import resolve_image_spatial_coordinates

    dataset = config["dataset"]
    settings = config.get("roi_generation") or {}
    h5ad_path = _resolve_path(dataset["h5ad_path"], config_path.parent)
    image_path = _resolve_path(dataset["image_path"], config_path.parent)
    if not h5ad_path.is_file():
        raise FileNotFoundError(f"Spatial-expression dataset not found: {h5ad_path}")
    if not image_path.is_file():
        raise FileNotFoundError(f"H&E image not found: {image_path}")

    count = int(settings.get("count", 10))
    seed = int(settings.get("seed", 42))
    size = int(settings.get("roi_size_pixels", 256))
    min_spots = int(settings.get("min_spots", 100))
    min_distance = float(settings.get("min_center_distance_pixels", size))
    max_attempts = int(settings.get("max_attempts", 100000))
    fingerprint = _dataset_fingerprint(h5ad_path, image_path)
    cache_key = {
        "dataset": fingerprint, "count": count, "seed": seed, "size": size,
        "min_spots": min_spots, "min_center_distance_pixels": min_distance,
    }
    cache_path = output_dir / "roi_fixtures.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("cache_key") == cache_key and len(cached.get("rois") or []) == count:
                return cached["rois"], {
                    **cached.get("dataset_metadata", {}), "roi_cache_reused": True,
                    "roi_cache_path": str(cache_path),
                }
        except (OSError, ValueError, TypeError):
            pass

    with Image.open(image_path) as image:
        width, height = image.size
    adata = ad.read_h5ad(h5ad_path, backed="r")
    try:
        resolution = resolve_image_spatial_coordinates(
            adata, image_size=[height, width]
        )
        coordinates = np.asarray(resolution.coordinates, dtype=np.float64)
        barcodes = np.asarray([str(value) for value in adata.obs_names])
    finally:
        if getattr(adata, "file", None) is not None:
            adata.file.close()

    half = size / 2.0
    eligible = np.flatnonzero(
        np.isfinite(coordinates[:, 0]) & np.isfinite(coordinates[:, 1])
        & (coordinates[:, 0] >= half) & (coordinates[:, 0] <= width - half)
        & (coordinates[:, 1] >= half) & (coordinates[:, 1] <= height - half)
    )
    if eligible.size == 0:
        raise ValueError("No real spatial spots can center an in-bounds ROI.")

    selected_indices: list[int] = []
    origins: list[str] = []
    saved = settings.get("saved_roi") or []
    if saved:
        points = np.asarray([point for ring in saved for point in ring], dtype=float)
        saved_center = points[:, :2].mean(axis=0)
        distances = np.sum((coordinates[eligible] - saved_center) ** 2, axis=1)
        selected_indices.append(int(eligible[int(np.argmin(distances))]))
        origins.append("saved_roi_nearest_real_spot")

    rng = np.random.default_rng(seed)
    attempts = 0
    for raw_index in rng.permutation(eligible):
        if len(selected_indices) >= count or attempts >= max_attempts:
            break
        attempts += 1
        index = int(raw_index)
        center = coordinates[index]
        if any(np.linalg.norm(center - coordinates[prior]) < min_distance for prior in selected_indices):
            continue
        _, bounds = _square(center, size)
        if int(_spot_mask(coordinates, bounds).sum()) < min_spots:
            continue
        selected_indices.append(index)
        origins.append("seeded_real_spot")

    rois: list[dict[str, Any]] = []
    for number, (index, origin) in enumerate(zip(selected_indices, origins), start=1):
        polygon, bounds = _square(coordinates[index], size)
        mask = _spot_mask(coordinates, bounds)
        spot_count = int(mask.sum())
        if spot_count < min_spots:
            continue
        rois.append({
            "roi_id": f"real_roi_{number:02d}",
            "selection_type": "polygon",
            "source": origin,
            "center_spot_index": index,
            "center_spot_barcode": str(barcodes[index]),
            "center": coordinates[index].tolist(),
            "bounds": bounds,
            "polygon_points": polygon,
            "spot_count": spot_count,
        })
    if len(rois) != count:
        raise RuntimeError(
            f"Could only generate {len(rois)} valid real ROIs after {attempts} attempts; "
            f"needed {count}."
        )

    metadata = {
        **fingerprint,
        "image_width": width,
        "image_height": height,
        "total_spots": int(coordinates.shape[0]),
        "coordinate_source": resolution.source,
        "spot_diameter_pixels": resolution.spot_diameter,
        "roi_seed": seed,
        "roi_size_pixels": size,
        "minimum_spots": min_spots,
        "sampling_attempts": attempts,
        "roi_cache_reused": False,
        "roi_cache_path": str(cache_path),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"cache_key": cache_key, "dataset_metadata": metadata, "rois": rois}, indent=2),
        encoding="utf-8",
    )
    return rois, metadata


def _default_agent_runner(**kwargs: Any) -> Any:
    from rag.copilot_agent import run_copilot_agent
    return run_copilot_agent(**kwargs)


def _default_model_runner(messages: list[dict[str, Any]], provider: str, model: str | None) -> str:
    from app.inference import run_model_inference
    return "".join(run_model_inference(messages, provider=provider, model_name=model)).strip()


def _model_error(text: str) -> bool:
    from app.inference import is_inference_error

    return is_inference_error(text)


def _tool_payload(result: Any, tool: str) -> dict[str, Any]:
    outcome = (getattr(result, "tool_outcomes", {}) or {}).get(tool) or {}
    payload = outcome.get("result") if isinstance(outcome, dict) else None
    return payload if isinstance(payload, dict) else {}


def _trace_validation(trace: list[dict[str, Any]], result: Any, final_status: str) -> dict[str, Any]:
    tools = list(getattr(result, "tools_called", []) or []) if result is not None else []
    steps = [str(item.get("step") or "").lower() for item in trace]
    validation = {
        "retrieve": any("loaded region gene expression" in step for step in steps),
        "route": any(step.startswith("routed question as:") for step in steps),
        "tool_call": bool(tools) and all(any(item.get("tool") == tool for item in trace) for tool in tools),
        "synthesize": any("synthes" in step for step in steps),
        "classified_intent": str(getattr(result, "intent", "") or ""),
        "tools_executed": tools,
        "tool_call_count": len(tools),
        "final_status": final_status,
    }
    validation["valid"] = bool(
        validation["retrieve"] and validation["route"] and validation["tool_call"]
        and validation["synthesize"] and validation["classified_intent"]
        and validation["final_status"]
    )
    return validation


def execute_roi(
    roi_fixture: dict[str, Any], *, config: dict[str, Any], config_path: Path,
    output_dir: Path, provider: str, model: str | None,
    agent_runner: AgentRunner = _default_agent_runner,
    model_runner: ModelRunner = _default_model_runner,
    judge_enabled: bool = True,
) -> dict[str, Any]:
    """Run one real ROI; all failures are captured in the returned record."""

    from app.config import load_config
    from rag.copilot_agent.multimodal import model_supports_vision
    from rag.deg import run_roi_deg
    from rag.pipeline import prepare_roi_image_for_llm

    dataset = config["dataset"]
    h5ad_path = _resolve_path(dataset["h5ad_path"], config_path.parent)
    image_path = _resolve_path(dataset["image_path"], config_path.parent)
    question = str(config.get("question") or DEFAULT_QUESTION)
    disease = str(dataset.get("tissue_context") or "")
    errors: list[str] = []
    phase_timing: dict[str, float | None] = {}
    started = time.perf_counter()
    result = None
    answer = ""
    crop_payload: dict[str, Any] = {}
    deg_payload: dict[str, Any] = {}
    vision_capable = False

    try:
        import anndata as ad
        from rag.deg.coordinates import resolve_image_spatial_coordinates
        adata = ad.read_h5ad(h5ad_path, backed="r")
        try:
            from PIL import Image
            with Image.open(image_path) as image:
                width, height = image.size
            coords = resolve_image_spatial_coordinates(
                adata, image_size=[height, width]
            ).coordinates
            mask = _spot_mask(np.asarray(coords), roi_fixture["bounds"])
        finally:
            if getattr(adata, "file", None) is not None:
                adata.file.close()

        phase = time.perf_counter()
        analysis = config.get("analysis") or {}
        deg_result = run_roi_deg(str(h5ad_path), mask, {
            "top_n": int(analysis.get("top_n_genes", 25)),
            "min_cells": int(analysis.get("min_cells", 0)),
            "fdr_threshold": analysis.get("fdr_threshold"),
            "normalize": bool(analysis.get("normalize", False)),
            "run_statistical_test": bool(analysis.get("run_statistical_test", False)),
        })
        phase_timing["deg_seconds"] = time.perf_counter() - phase
        deg_payload = _json_safe(deg_result)
        genes = list(deg_payload.get("top_genes") or [])
        if not genes:
            errors.append(deg_payload.get("status_message") or "DEG returned no genes.")

        phase = time.perf_counter()
        crop_result = prepare_roi_image_for_llm(
            str(image_path),
            {"roi_id": roi_fixture["roi_id"], "selection_type": "polygon",
             "polygon_points": roi_fixture["polygon_points"]},
            {"output_dir": str(output_dir / "roi_crops"), "max_dimension": 1536},
        )
        phase_timing["image_crop_seconds"] = time.perf_counter() - phase
        crop_payload = _json_safe(crop_result)
        crop_path = str(crop_payload.get("crop_path") or "")
        if not crop_path:
            errors.append(crop_payload.get("status_message") or "ROI crop failed.")

        app_config = load_config()
        vision_capable = bool(model_supports_vision(model, app_config))
        phase = time.perf_counter()
        result = agent_runner(
            question=question,
            roi={
                "roi_id": roi_fixture["roi_id"], "selection_type": "polygon",
                "polygon_points": roi_fixture["polygon_points"],
                "spot_ids": range(int(roi_fixture["spot_count"])),
            },
            roi_image=crop_payload,
            deg=genes,
            config=app_config,
            label=roi_fixture["roi_id"],
            image_attached=bool(crop_path and vision_capable),
            max_tool_calls=int((config.get("agent") or {}).get("max_tool_calls", 5)),
            semantic_rerank=bool((config.get("agent") or {}).get("semantic_rerank", False)),
            disease=disease or None,
        )
        phase_timing["agent_backend_seconds"] = time.perf_counter() - phase

        context = str(getattr(result, "context_str", "") or "")
        message: dict[str, Any] = {"role": "user", "content": question + context}
        if crop_path and vision_capable:
            message["images"] = [crop_path]
        phase = time.perf_counter()
        answer = model_runner([message], provider, model).strip()
        phase_timing["llm_generation_seconds"] = time.perf_counter() - phase
        if not answer or _model_error(answer):
            errors.append(answer or "Model returned an empty answer.")
            answer = ""
    except Exception as exc:  # Keep the remaining nine ROIs runnable.
        errors.append(f"{type(exc).__name__}: {exc}")

    copilot_seconds = time.perf_counter() - started
    trace = [
        step.to_dict() if hasattr(step, "to_dict") else dict(step)
        for step in (getattr(result, "trace", []) or [])
    ]
    trace.append({
        "step": "Synthesized final response" if answer else "Final response synthesis failed",
        "detail": f"{provider}:{model or 'default'}", "icon": "agent",
        "tool": provider, "status": "ok" if answer else "error",
        "input_summary": "question plus ROI image and retrieved evidence",
        "output_summary": f"{len(answer)} characters" if answer else "; ".join(errors),
    })
    tools = list(getattr(result, "tools_called", []) or []) if result is not None else []
    outcomes = getattr(result, "tool_outcomes", {}) or {} if result is not None else {}
    tool_calls = summarize_tool_calls(tools, outcomes, trace)
    tool_failures = [call for call in tool_calls if call.get("failure")]
    final_status = "error" if not answer else ("partial" if tool_failures else "completed")

    record: dict[str, Any] = {
        "schema_version": "3.0",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "roi_id": roi_fixture["roi_id"],
        "roi": {**roi_fixture, "crop_path": crop_payload.get("crop_path", "")},
        "query": question,
        "tissue_context": disease,
        "provider": provider,
        "model": model or "",
        "vision_capable": vision_capable,
        "deg": deg_payload,
        "gene_annotations": _tool_payload(result, "gene_annotation_tool") if result else {},
        "pathways": _tool_payload(result, "pathway_tool") if result else {},
        "pubmed": _tool_payload(result, "pubmed_tool") if result else {},
        "answer": answer,
        "agent": {
            "classified_intent": str(getattr(result, "intent", "") or "") if result else "",
            "tools_executed": tools,
            "tool_call_count": len(tools),
            "tool_calls": tool_calls,
            "trace": trace,
            "status": final_status,
        },
        "timing": {
            "copilot_end_to_end_seconds": copilot_seconds,
            "backend_seconds": (
                sum(value for key, value in phase_timing.items() if key != "llm_generation_seconds" and value is not None)
            ),
            "llm_seconds": phase_timing.get("llm_generation_seconds"),
            "production_phase_seconds": phase_timing,
            "production_tool_timing": "not exposed by the existing tool contract",
        },
        "workflow_efficiency": {
            "automatically_connected_analysis_retrieval_stages": [
                stage
                for stage, reached in (
                    ("H&E ROI crop", bool(crop_payload.get("crop_path"))),
                    ("ROI DEG", bool(deg_payload.get("top_genes"))),
                    ("NCBI Gene annotation", "gene_annotation_tool" in tools),
                    ("Enrichr pathway enrichment", "pathway_tool" in tools),
                    ("PubMed retrieval", "pubmed_tool" in tools),
                )
                if reached
            ],
            "automatically_connected_stage_count": sum(
                bool(reached)
                for reached in (
                    crop_payload.get("crop_path"),
                    deg_payload.get("top_genes"),
                    "gene_annotation_tool" in tools,
                    "pathway_tool" in tools,
                    "pubmed_tool" in tools,
                )
            ),
            "tool_calls_executed": len(tools),
            "manual_data_reentry_steps": 0,
        },
        "errors": list(dict.fromkeys(error for error in errors if error)),
    }
    record["agent_trace_validation"] = _trace_validation(trace, result, final_status)

    if judge_enabled and answer:
        try:
            judge = JudgeClient(model_runner=model_runner, provider=provider, model=model)
            record["judgments"] = judge_roi(record, judge)
        except Exception as exc:
            judge_error = f"Judge failed: {type(exc).__name__}: {exc}"
            record["errors"] = list(dict.fromkeys([*record.get("errors", []), judge_error]))
            record["judgments"] = {
                "status": "error",
                "text": {},
                "vision": {},
                "raw_text_judge_outputs": [],
                "raw_vision_judge_outputs": [],
                "errors": [judge_error],
            }
    else:
        reason = (
            "Judging disabled by --no-judge."
            if not judge_enabled
            else "Judging skipped because no answer was generated."
        )
        record["judgments"] = {
            "status": "skipped", "text": {}, "vision": {}, "errors": [reason]
        }
    return record


def _resolve_provider_model(provider: str | None, model: str | None) -> tuple[str, str | None]:
    from app.config import load_dotenv
    from app.inference import get_default_model_spec
    load_dotenv()
    configured_provider, configured_model = get_default_model_spec().split(":", 1)
    selected_provider = str(provider or configured_provider).strip().lower()
    selected_model = model if model is not None else configured_model
    if selected_provider not in {"ollama", "deepinfra"}:
        raise ValueError(f"Unsupported provider: {selected_provider}")
    return selected_provider, selected_model or None


def _unhandled_roi_failure_record(
    roi: dict[str, Any], *, provider: str, model: str | None, exc: Exception,
) -> dict[str, Any]:
    """Serialize an unexpected per-ROI failure instead of aborting the 10-ROI run."""

    error = f"Unhandled ROI failure: {type(exc).__name__}: {exc}"
    return {
        "schema_version": "3.0",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "roi_id": roi.get("roi_id", "unknown_roi"),
        "roi": dict(roi),
        "query": "",
        "tissue_context": "",
        "provider": provider,
        "model": model or "",
        "vision_capable": False,
        "deg": {},
        "gene_annotations": {},
        "pathways": {},
        "pubmed": {},
        "answer": "",
        "agent": {
            "classified_intent": "",
            "tools_executed": [],
            "tool_call_count": 0,
            "tool_calls": [],
            "trace": [],
            "status": "error",
        },
        "timing": {
            "copilot_end_to_end_seconds": None,
            "backend_seconds": None,
            "llm_seconds": None,
            "production_phase_seconds": {},
            "production_tool_timing": "not available because ROI execution failed unexpectedly",
        },
        "workflow_efficiency": {
            "automatically_connected_analysis_retrieval_stages": [],
            "automatically_connected_stage_count": 0,
            "tool_calls_executed": 0,
            "manual_data_reentry_steps": 0,
        },
        "errors": [error],
        "agent_trace_validation": {
            "retrieve": False, "route": False, "tool_call": False,
            "synthesize": False, "classified_intent": "",
            "tools_executed": [], "tool_call_count": 0,
            "final_status": "error", "valid": False,
        },
        "judgments": {
            "status": "skipped", "text": {}, "vision": {},
            "errors": ["Judging skipped because ROI execution failed unexpectedly."],
        },
    }


def run_evaluation(
    config_path: str | Path, output_dir: str | Path, *,
    provider: str | None = None, model: str | None = None,
    agent_runner: AgentRunner = _default_agent_runner,
    model_runner: ModelRunner = _default_model_runner,
    judge_enabled: bool = True,
) -> list[dict[str, Any]]:
    config_path = Path(config_path).resolve()
    output_dir = Path(output_dir).resolve()
    config = load_cases(config_path)
    provider, model = _resolve_provider_model(provider, model)
    rois, dataset_metadata = generate_real_rois(config, config_path, output_dir)

    records: list[dict[str, Any]] = []
    for index, roi in enumerate(rois, start=1):
        print(
            f"[{index}/{len(rois)}] Evaluating {roi['roi_id']} "
            f"({roi['spot_count']} spots)...",
            flush=True,
        )
        try:
            record = execute_roi(
                roi, config=config, config_path=config_path, output_dir=output_dir,
                provider=provider, model=model, agent_runner=agent_runner,
                model_runner=model_runner, judge_enabled=judge_enabled,
            )
        except Exception as exc:
            # Last-resort isolation: one bad ROI must not terminate the other nine.
            record = _unhandled_roi_failure_record(
                roi, provider=provider, model=model, exc=exc
            )
            print(
                f"[{index}/{len(rois)}] {roi['roi_id']} failed but the run will continue: "
                f"{type(exc).__name__}: {exc}",
                file=sys.stderr, flush=True,
            )
        records.append(record)

        # Persist after every ROI so an interrupted long run remains auditable.
        metrics = aggregate_proposal_metrics(records)
        write_outputs(output_dir, records, metrics, {
            "dataset": dataset_metadata, "provider": provider, "model": model or "",
            "config_path": str(config_path), "expected_roi_count": len(rois),
            "judge_enabled": judge_enabled,
        })
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(PROJECT_ROOT / "evaluation" / "eval_cases.json"))
    parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "evaluation_outputs"))
    parser.add_argument("--provider", choices=("ollama", "deepinfra"))
    parser.add_argument("--model")
    parser.add_argument("--no-judge", action="store_true", help="Development only: run production workflow without LLM judging.")
    args = parser.parse_args(argv)
    records = run_evaluation(
        args.config, args.output_dir, provider=args.provider, model=args.model,
        judge_enabled=not args.no_judge,
    )
    failures = sum(bool(record.get("errors")) for record in records)
    print(f"Evaluation complete: {len(records)} real ROI(s), {failures} with workflow errors.")
    print(f"Outputs: {Path(args.output_dir).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
