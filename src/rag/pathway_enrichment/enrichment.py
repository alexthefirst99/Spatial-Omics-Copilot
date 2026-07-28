"""Real pathway over-representation analysis through Enrichr/GSEApy."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import importlib
import re
from typing import Any

from .models import PathwayEntry, PathwayResult

DEFAULT_GENE_SETS = ("GO_Biological_Process_2023", "KEGG_2021_Human")


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

    try:
        gseapy = importlib.import_module("gseapy")
        enrichr_result = gseapy.enrichr(
            gene_list=input_genes,
            gene_sets=gene_sets,
            organism=organism,
            outdir=None,
            cutoff=cutoff,
            no_plot=True,
            verbose=False,
        )
        pathways, valid_rows = _records_from_enrichr(
            enrichr_result,
            gene_sets=gene_sets,
            adjusted_p_value_cutoff=cutoff,
            significant_only=significant_only,
            top_n=top_n,
        )
    except Exception as exc:  # GSEApy wraps network failures in several classes.
        detail = " ".join(str(exc).split()) or exc.__class__.__name__
        return PathwayResult(
            pathways=[],
            status_message=f"Pathway enrichment unavailable: {detail}",
            input_genes=input_genes,
            sources=gene_sets,
        )

    if pathways:
        return PathwayResult(
            pathways=pathways,
            status_message=f"Retrieved {len(pathways)} enriched pathway(s) from Enrichr.",
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
    return PathwayResult(
        pathways=[],
        status_message=status,
        input_genes=input_genes,
        sources=gene_sets,
    )


__all__ = ["DEFAULT_GENE_SETS", "run_pathway_enrichment"]
