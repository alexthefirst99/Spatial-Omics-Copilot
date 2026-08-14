"""Real pathway over-representation analysis through Enrichr."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from io import StringIO
import re
from types import SimpleNamespace
from typing import Any

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import PathwayEntry, PathwayResult

DEFAULT_GENE_SETS = ("GO_Biological_Process_2023", "KEGG_2021_Human")
ENRICHR_URL = "https://maayanlab.cloud/Enrichr"


def _error_detail(exc: BaseException) -> str:
    return " ".join(str(exc).split()) or exc.__class__.__name__


def _enrichr_json_frame(payload: object, gene_set: str) -> pd.DataFrame:
    """Convert Enrichr's JSON response to the same columns as ``/export``."""

    if not isinstance(payload, Mapping):
        raise ValueError("Enrichr returned a non-object JSON response")
    rows = payload.get(gene_set)
    if not isinstance(rows, list):
        raise ValueError(f"Enrichr response did not contain {gene_set}")

    columns = [
        "Rank",
        "Term",
        "P-value",
        "Odds Ratio",
        "Combined Score",
        "Genes",
        "Adjusted P-value",
        "Old P-value",
        "Old Adjusted P-value",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    if not frame.empty:
        frame["Genes"] = frame["Genes"].apply(
            lambda value: ";".join(value) if isinstance(value, list) else value
        )
    return frame


def _fetch_enrichr_frames(
    genes: Sequence[str],
    gene_sets: Sequence[str],
    *,
    timeout: float,
    max_retries: int,
) -> tuple[list[pd.DataFrame], list[str]]:
    """Submit genes once and fetch each library over Enrichr's HTTPS API.

    The pinned GSEApy 1.1.2 client uses a legacy HTTP URL and only falls back
    from ``/export`` for one particular pandas parser exception. A proxy or an
    Enrichr error page therefore produces ``Error fetching enrichment
    results`` even when the JSON endpoint is available. This client validates
    the export response and always tries the JSON endpoint as a fallback.
    """

    retry = Retry(
        total=max(0, max_retries),
        connect=max(0, max_retries),
        read=max(0, max_retries),
        status=max(0, max_retries),
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))

    try:
        response = session.post(
            f"{ENRICHR_URL}/addList",
            files={
                "list": (None, "\n".join(genes)),
                "description": (None, "Spatial Omics Copilot pathway enrichment"),
            },
            timeout=timeout,
        )
        response.raise_for_status()
        job = response.json()
        user_list_id = job.get("userListId") if isinstance(job, Mapping) else None
        if user_list_id is None:
            raise ValueError("Enrichr did not return a userListId")
    except Exception as exc:  # noqa: BLE001 - normalized for the safe result envelope
        session.close()
        return [], [f"gene-list submission: {_error_detail(exc)}"]

    frames: list[pd.DataFrame] = []
    errors: list[str] = []
    for gene_set in gene_sets:
        params = {"userListId": user_list_id, "backgroundType": gene_set}
        export_error: BaseException | None = None
        try:
            response = session.get(
                f"{ENRICHR_URL}/export",
                params={**params, "filename": f"{gene_set}.reports"},
                timeout=timeout,
            )
            response.raise_for_status()
            frame = pd.read_csv(StringIO(response.text), sep="\t")
            if "Term" not in frame.columns or not any(
                re.sub(r"[^a-z0-9]", "", str(column).casefold())
                == "adjustedpvalue"
                for column in frame.columns
            ):
                raise ValueError("Enrichr export response had an unexpected format")
        except Exception as exc:  # noqa: BLE001 - JSON is the supported fallback
            export_error = exc
            try:
                response = session.get(
                    f"{ENRICHR_URL}/enrich",
                    params=params,
                    timeout=timeout,
                )
                response.raise_for_status()
                frame = _enrichr_json_frame(response.json(), gene_set)
            except Exception as json_exc:  # noqa: BLE001 - isolate failures by library
                errors.append(
                    f"{gene_set}: export failed ({_error_detail(export_error)}); "
                    f"JSON fallback failed ({_error_detail(json_exc)})"
                )
                continue

        if "Gene_set" not in frame.columns:
            frame.insert(0, "Gene_set", gene_set)
        # A successful empty response is different from an unavailable API.
        frames.append(frame)

    session.close()
    return frames, errors


def _normalise_genes(genes: object, *, max_genes: int | None = None) -> list[str]:
    if isinstance(genes, str):
        genes = [genes]
    try:
        candidates = list(genes)  # type: ignore[arg-type]
    except TypeError:
        candidates = []

    output: list[str] = []
    seen: set[str] = set()
    for value in candidates:
        gene = " ".join(str(value).split()).upper() if value is not None else ""
        if not gene or gene in seen:
            continue
        seen.add(gene)
        output.append(gene)
        if max_genes is not None and len(output) >= max_genes:
            break
    return output


def _config_value(config: object, dotted_keys: Sequence[str], default: Any) -> Any:
    for dotted_key in dotted_keys:
        current = config
        found = True
        for part in dotted_key.split("."):
            if isinstance(current, Mapping):
                if part not in current:
                    found = False
                    break
                current = current[part]
            else:
                if not hasattr(current, part):
                    found = False
                    break
                current = getattr(current, part)
        if found and current is not None:
            return current
    return default


def _column_lookup(row: Mapping[str, Any], *aliases: str, default: Any = None) -> Any:
    canonical = {
        re.sub(r"[^a-z0-9]", "", str(key).casefold()): value
        for key, value in row.items()
    }
    for alias in aliases:
        key = re.sub(r"[^a-z0-9]", "", alias.casefold())
        if key in canonical:
            return canonical[key]
    return default


def _safe_float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_overlap(value: object) -> tuple[int | None, int | None]:
    text = "" if value is None else str(value).strip()
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", text)
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _parse_overlap_genes(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = re.split(r"[;,]", value)
    else:
        try:
            values = list(value)  # type: ignore[arg-type]
        except TypeError:
            values = []
    genes = _normalise_genes(values)
    return tuple(genes)


def _records_from_enrichr(
    enrichr_result: object,
    *,
    gene_sets: Sequence[str],
    adjusted_p_value_cutoff: float,
    significant_only: bool,
    top_n: int,
) -> tuple[list[PathwayEntry], int]:
    frame = getattr(enrichr_result, "res2d", None)
    if frame is None:
        frame = getattr(enrichr_result, "results", None)
    if frame is None or not hasattr(frame, "to_dict"):
        return [], 0

    rows = frame.to_dict(orient="records")
    pathways: list[PathwayEntry] = []
    seen: set[tuple[str, str]] = set()
    valid_rows = 0

    for raw_row in rows:
        if not isinstance(raw_row, Mapping):
            continue
        row = dict(raw_row)
        name = str(_column_lookup(row, "Term", "Pathway", "Name", default="")).strip()
        adjusted = _safe_float(
            _column_lookup(
                row,
                "Adjusted P-value",
                "Adjusted_P-value",
                "Adjusted P value",
                "FDR",
                "q-value",
            )
        )
        if not name or adjusted is None:
            continue
        valid_rows += 1
        if significant_only and adjusted > adjusted_p_value_cutoff:
            continue

        source = str(
            _column_lookup(row, "Gene_set", "Gene Set", "Source", default="")
        ).strip()
        if not source and len(gene_sets) == 1:
            source = str(gene_sets[0])
        dedupe_key = (source.casefold(), name.casefold())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        overlap_count, gene_set_size = _parse_overlap(
            _column_lookup(row, "Overlap", default="")
        )
        overlap_genes = _parse_overlap_genes(
            _column_lookup(row, "Genes", "Overlap genes", default="")
        )
        pathways.append(
            PathwayEntry(
                name=name,
                adjusted_p_value=adjusted,
                overlap_genes=overlap_genes,
                source=source,
                nominal_p_value=_safe_float(
                    _column_lookup(row, "P-value", "P value")
                ),
                odds_ratio=_safe_float(_column_lookup(row, "Odds Ratio")),
                combined_score=_safe_float(_column_lookup(row, "Combined Score")),
                overlap_count=overlap_count,
                gene_set_size=gene_set_size,
            )
        )

    pathways.sort(key=lambda item: (item.adjusted_p_value, item.name.casefold()))
    return pathways[:top_n], valid_rows


def run_pathway_enrichment(
    genes: list[str],
    config: object | None = None,
) -> PathwayResult:
    """Run Enrichr ORA for the top ROI genes.

    Defaults follow ticket T-012: GO Biological Process 2023 and KEGG 2021
    Human. All failures return a safe ``PathwayResult`` instead of raising into
    the web request/background worker.
    """

    max_genes = int(
        _config_value(
            config,
            ("pathway_enrichment.max_genes", "pathway.max_genes", "max_genes"),
            100,
        )
    )
    input_genes = _normalise_genes(genes, max_genes=max(1, max_genes))
    if not input_genes:
        return PathwayResult(
            pathways=[],
            status_message="No genes provided for pathway enrichment.",
            input_genes=[],
            sources=list(DEFAULT_GENE_SETS),
        )

    raw_gene_sets = _config_value(
        config,
        ("pathway_enrichment.gene_sets", "pathway.gene_sets", "gene_sets"),
        list(DEFAULT_GENE_SETS),
    )
    if isinstance(raw_gene_sets, str):
        gene_sets = [raw_gene_sets]
    else:
        try:
            gene_sets = [str(item).strip() for item in raw_gene_sets if str(item).strip()]
        except TypeError:
            gene_sets = list(DEFAULT_GENE_SETS)
    if not gene_sets:
        gene_sets = list(DEFAULT_GENE_SETS)

    organism = str(
        _config_value(
            config,
            ("pathway_enrichment.organism", "pathway.organism", "organism"),
            "Human",
        )
    )
    top_n = max(
        1,
        int(
            _config_value(
                config,
                ("pathway_enrichment.top_n", "pathway.top_n", "top_n"),
                10,
            )
        ),
    )
    cutoff = float(
        _config_value(
            config,
            (
                "pathway_enrichment.adjusted_p_value_cutoff",
                "pathway.adjusted_p_value_cutoff",
                "adjusted_p_value_cutoff",
            ),
            0.05,
        )
    )
    significant_only = bool(
        _config_value(
            config,
            (
                "pathway_enrichment.significant_only",
                "pathway.significant_only",
                "significant_only",
            ),
            True,
        )
    )
    timeout = max(
        1.0,
        float(
            _config_value(
                config,
                ("pathway_enrichment.timeout", "pathway.timeout", "timeout"),
                30,
            )
        ),
    )
    max_retries = max(
        0,
        int(
            _config_value(
                config,
                (
                    "pathway_enrichment.max_retries",
                    "pathway.max_retries",
                    "max_retries",
                ),
                2,
            )
        ),
    )

    try:
        # Enrichr's standard API serves the Human/Mouse libraries configured
        # here. Retain the organism validation that GSEApy previously provided.
        if organism.strip().casefold() not in {
            "human",
            "mouse",
            "homo sapiens",
            "mus musculus",
            "h. sapiens",
            "m. musculus",
        }:
            raise ValueError(f"Unsupported Enrichr organism: {organism}")

        frames, errors = _fetch_enrichr_frames(
            input_genes,
            gene_sets,
            timeout=timeout,
            max_retries=max_retries,
        )

        if not frames:
            detail = "; ".join(errors) or "no results returned"
            return PathwayResult(
                pathways=[],
                status_message=f"Pathway enrichment unavailable: {detail}",
                input_genes=input_genes,
                sources=gene_sets,
            )

        combined_frame = (
            pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        )
        pathways, valid_rows = _records_from_enrichr(
            SimpleNamespace(res2d=combined_frame),
            gene_sets=gene_sets,
            adjusted_p_value_cutoff=cutoff,
            significant_only=significant_only,
            top_n=top_n,
        )
    except Exception as exc:  # Requests/pandas expose several failure classes.
        detail = _error_detail(exc)
        return PathwayResult(
            pathways=[],
            status_message=f"Pathway enrichment unavailable: {detail}",
            input_genes=input_genes,
            sources=gene_sets,
        )

    if pathways:
        status = f"Retrieved {len(pathways)} enriched pathway(s) from Enrichr."
        if errors:
            status += f" Some libraries were unavailable: {'; '.join(errors)}"
        return PathwayResult(
            pathways=pathways,
            status_message=status,
            input_genes=input_genes,
            sources=gene_sets,
        )
    if valid_rows and significant_only:
        status = (
            "Enrichr returned pathway results, but none passed the adjusted "
            f"p-value cutoff of {cutoff:g}."
        )
    else:
        status = "No enriched pathways were returned by Enrichr."
    if errors:
        status += f" Some libraries were unavailable: {'; '.join(errors)}"
    return PathwayResult(
        pathways=[],
        status_message=status,
        input_genes=input_genes,
        sources=gene_sets,
    )


__all__ = ["DEFAULT_GENE_SETS", "run_pathway_enrichment"]
