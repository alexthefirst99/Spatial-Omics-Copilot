"""One-command full benchmark runner with demo-data preflight and JSON metrics."""

from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.report import aggregate_metrics
from evaluation.runner import PROJECT_ROOT, load_cases, run_evaluation


DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "demo"
DEFAULT_CONFIG = PROJECT_ROOT / "evaluation" / "eval_cases.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "evaluation_outputs_full"


def _preferred_file(paths: list[Path], preferred_suffix: str) -> Path | None:
    if not paths:
        return None
    preferred = [path for path in paths if path.name.endswith(preferred_suffix)]
    return sorted(preferred or paths)[0]


def discover_demo_assets(data_dir: str | Path = DEFAULT_DATA_DIR) -> dict[str, Any]:
    """Find the default expression and slide files without loading 1.4 GB of data."""

    directory = Path(data_dir).expanduser().resolve()
    if not directory.is_dir():
        raise FileNotFoundError(f"Demo data directory not found: {directory}")

    h5ad = _preferred_file(list(directory.glob("*.h5ad")), "_feature_slice.h5ad")
    images = sorted({*directory.glob("*.tif"), *directory.glob("*.tiff")})
    image = _preferred_file(images, "_image.tif")
    missing = []
    if h5ad is None:
        missing.append("a .h5ad expression file")
    if image is None:
        missing.append("a .tif/.tiff slide image")
    if missing:
        raise FileNotFoundError(
            f"Demo data under {directory} is missing " + " and ".join(missing) + "."
        )

    def describe(path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "path": str(path),
            "filename": path.name,
            "size_bytes": stat.st_size,
            "modified_utc": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
        }

    return {
        "data_dir": str(directory),
        "h5ad": describe(h5ad),
        "image": describe(image),
        # The audited benchmark deliberately uses controlled inline fixtures.
        # Record this explicitly so the demo preflight is never mistaken for
        # evidence that the 28 cases are real-ROI biological validation.
        "benchmark_case_source": "synthetic_inline_fixtures",
        "demo_assets_used_for_case_inputs": False,
        "note": (
            "Demo assets are validated as the application's default dataset. "
            "The controlled benchmark remains synthetic because no audited ROI "
            "polygons or biological ground-truth labels are supplied with the demo."
        ),
    }


def resolve_generation(
    config_path: str | Path,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> tuple[str, str | None]:
    """Resolve provider/model from CLI, then .env, then evaluation config."""

    from app.config import load_config, load_dotenv

    load_dotenv()
    evaluation_config = load_cases(Path(config_path).resolve())
    generation = evaluation_config.get("generation") or {}
    app_config = load_config()

    selected_provider = str(
        provider or os.getenv("LLM_PROVIDER") or generation.get("provider") or "ollama"
    ).strip().lower()
    if selected_provider not in {"ollama", "deepinfra"}:
        raise ValueError(
            f"Unsupported provider {selected_provider!r}; use 'ollama' or 'deepinfra'."
        )

    if model:
        selected_model = model.strip()
    elif selected_provider == "deepinfra":
        selected_model = str(
            os.getenv("DEEPINFRA_MODEL")
            or os.getenv("LLM_MODEL")
            or (app_config.get("deepinfra") or {}).get("model")
            or ""
        ).strip()
        if not selected_model:
            raise ValueError(
                "DeepInfra is selected but no model is configured. Set "
                "DEEPINFRA_MODEL in .env or pass --model."
            )
    else:
        selected_model = str(
            os.getenv("OLLAMA_MODEL")
            or generation.get("model")
            or (app_config.get("ollama") or {}).get("model")
            or ""
        ).strip()

    return selected_provider, selected_model or None


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def build_full_metrics(
    records: list[dict[str, Any]],
    *,
    provider: str,
    model: str | None,
    demo_assets: dict[str, Any],
    config_path: Path,
) -> dict[str, Any]:
    """Build one machine-readable envelope containing every automatic metric."""

    overall = aggregate_metrics(records)
    categories = sorted({str(record.get("category") or "uncategorized") for record in records})
    category_metrics = {
        category: aggregate_metrics(
            [record for record in records if record.get("category") == category]
        )
        for category in categories
    }
    route_mismatches = [
        record.get("case_id")
        for record in records
        if record.get("expected_route") != record.get("actual_route")
    ]
    tool_set_mismatches = [
        record.get("case_id")
        for record in records
        if list(record.get("expected_tools") or [])
        != list(record.get("tools_actually_called") or [])
    ]
    tool_statuses = Counter(
        f"{call.get('tool')}::{call.get('status')}"
        for record in records
        for call in (record.get("tool_calls") or [])
    )
    generated = [record for record in records if record.get("final_answer")]
    grounding = [
        float(value)
        for record in generated
        if isinstance(
            (value := (record.get("automatic_metrics") or {}).get(
                "evidence_grounding_score"
            )),
            (int, float),
        )
    ]
    negative_records = [
        record for record in records if record.get("expect_insufficient_evidence")
    ]
    acknowledged = [
        bool((record.get("automatic_metrics") or {}).get(
            "insufficient_evidence_acknowledged"
        ))
        for record in negative_records
        if record.get("final_answer")
    ]

    return {
        "schema_version": "1.0",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "benchmark_config": str(config_path.resolve()),
        "provider": provider,
        "model": model or "",
        "demo_data": demo_assets,
        "overall": overall,
        "by_category": category_metrics,
        "quality_coverage": {
            "generated_answers": len(generated),
            "grounded_answers_scored": len(grounding),
            "average_evidence_grounding_score": _mean(grounding),
            "negative_answers_reviewed_automatically": len(acknowledged),
            "insufficient_evidence_acknowledgement_rate": (
                _mean([float(value) for value in acknowledged])
                if acknowledged
                else None
            ),
        },
        "contract_checks": {
            "route_mismatch_count": len(route_mismatches),
            "route_mismatch_case_ids": route_mismatches,
            "exact_tool_set_mismatch_count": len(tool_set_mismatches),
            "exact_tool_set_mismatch_case_ids": tool_set_mismatches,
            "run_error_case_ids": [
                record.get("case_id") for record in records if record.get("errors")
            ],
            "tool_failure_case_ids": [
                record.get("case_id")
                for record in records
                if record.get("tool_errors")
            ],
        },
        "tool_status_counts": dict(sorted(tool_statuses.items())),
        "per_case_automatic_metrics": {
            str(record.get("case_id")): record.get("automatic_metrics") or {}
            for record in records
        },
        "human_review_required": True,
        "clinical_validation": False,
    }


def run_full(
    *,
    data_dir: str | Path = DEFAULT_DATA_DIR,
    config_path: str | Path = DEFAULT_CONFIG,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    provider: str | None = None,
    model: str | None = None,
    generation_enabled: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate demo assets, run all cases, and write a consolidated metric JSON."""

    config_path = Path(config_path).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    demo_assets = discover_demo_assets(data_dir)
    selected_provider, selected_model = resolve_generation(
        config_path, provider=provider, model=model
    )
    records = run_evaluation(
        config_path,
        output_dir,
        provider=selected_provider,
        model=selected_model,
        generation_enabled=generation_enabled,
    )
    metrics = build_full_metrics(
        records,
        provider=selected_provider,
        model=selected_model,
        demo_assets=demo_assets,
        config_path=config_path,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "full_metrics.json").write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "dataset_manifest.json").write_text(
        json.dumps(demo_assets, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return records, metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--provider", choices=("ollama", "deepinfra"))
    parser.add_argument("--model")
    parser.add_argument(
        "--no-generation",
        action="store_true",
        help="Run routes and evidence tools only; full answer-quality metrics will be N/A.",
    )
    args = parser.parse_args(argv)

    try:
        records, metrics = run_full(
            data_dir=args.data_dir,
            config_path=args.config,
            output_dir=args.output_dir,
            provider=args.provider,
            model=args.model,
            generation_enabled=not args.no_generation,
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    output_dir = Path(args.output_dir).expanduser().resolve()
    run_errors = len(metrics["contract_checks"]["run_error_case_ids"])
    tool_failures = len(metrics["contract_checks"]["tool_failure_case_ids"])
    print(
        f"Full evaluation complete: {len(records)} cases, {run_errors} run error(s), "
        f"{tool_failures} case(s) with tool failures."
    )
    print(f"Provider/model: {metrics['provider']}:{metrics['model'] or 'default'}")
    print(f"Full metrics: {output_dir / 'full_metrics.json'}")
    print(f"Readable summary: {output_dir / 'evaluation_summary.md'}")
    print(f"Human review sheet: {output_dir / 'human_review.csv'}")
    return 1 if run_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
