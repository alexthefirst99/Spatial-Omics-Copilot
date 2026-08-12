"""Normalisation between upstream module outputs and the agent's own models.

This module exists because the agent sits downstream of four teammates' code
and one shared contract that does not exist yet. Inputs arrive in whatever
shape the current branch happens to produce:

* DEG payloads reach the agent as a bare ``list[dict]`` today (``app/app.py``
  writes ``top_genes`` straight into ``roi_context.json``), but Person 2's
  unmerged branch introduces ``DEGResult`` / ``GeneStat`` dataclasses, and
  Person 6's ``src/rag/contracts.py`` will introduce a third shape.
* ``PathwayEntry`` and ``GeneAnnotation`` support ``entry["key"]`` access;
  ``PubMedPaper`` and ``PubMedResult`` do **not** and raise TypeError if you try.
* ``retrieve_abstracts`` returns ``snippet``; ``search_pubmed`` returns
  ``abstract``. Neither returns both.

Everything here is duck-typed on purpose. In particular this module must never
``import rag.deg`` — that package imports ``anndata``, which is absent from the
interpreter the test suite runs on, and a module-level import would take every
agent test down at collection time.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite, log10
from typing import Any

from rag.copilot_agent.models import Citation, DegBar, PathwayBar

# Gene-symbol keys in specificity order.
_GENE_KEYS = ("gene", "gene_symbol", "symbol", "name")

# Fold-change keys, most specific first.
_FC_KEYS = ("log2_fold_change", "log2fc", "log2FoldChange", "lfc")

# Short tags fit the fixed-height source pill in chat.js.
_SOURCE_TAGS = (
    ("GO_", "GO"),
    ("GO:", "GO"),
    ("KEGG", "KEGG"),
    ("REACTOME", "Reactome"),
    ("WIKIPATHWAY", "WikiPath"),
    ("MSIGDB", "MSigDB"),
    ("HALLMARK", "MSigDB"),
    ("BIOCARTA", "BioCarta"),
)

# Ceiling for -log10(p): p is clamped at 1e-15 so a zero p-value cannot produce
# an infinity, which would break the bar chart's max() and .toFixed(1).
_MIN_P_VALUE = 1e-15


def _get(source: object, key: str, default: Any = None) -> Any:
    """Read ``key`` from a Mapping or an attribute-style object.

    ``PubMedPaper`` is not a Mapping and ``PathwayEntry`` is; this reads either
    without the caller having to know which.
    """

    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _text(value: object) -> str:
    """Return a clean single-line string, never ``None``."""

    if value is None:
        return ""
    return " ".join(str(value).split())


def _number(value: object, default: float = 0.0) -> float:
    """Coerce to a finite float, never ``None`` / ``NaN`` / infinity."""

    if isinstance(value, bool) or value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _first_key(source: object, keys: Sequence[str]) -> Any:
    """Return the first present, non-empty value among ``keys``."""

    for key in keys:
        value = _get(source, key)
        if value not in (None, ""):
            return value
    return None


# -- DEG payloads ----------------------------


def normalize_gene_objects(payload: object) -> list[dict[str, Any]]:
    """Coerce any DEG payload into the ``list[dict]`` the UI contract expects.

    Handles every shape the agent can be handed:

    * ``None`` or ``[]`` — no data loaded.
    * ``list[dict]`` — what ``routes.py`` passes today.
    * ``list[GeneStat]`` — Person 2's dataclass rows.
    * ``dict`` with a ``top_genes`` key — a whole legacy DEG result.
    * an object with a ``top_genes`` attribute — Person 2's ``DEGResult``.

    Args:
        payload: The DEG payload in any of the above shapes.

    Returns:
        A list of plain dicts, each guaranteed to carry a non-empty ``"gene"``
        key and a numeric ``"log2_fold_change"``. Rows without a resolvable
        gene symbol are dropped rather than emitted with ``"undefined"``.
    """

    if payload is None:
        return []

    # Unwrap an envelope (DEGResult, or a legacy result dict) down to its rows.
    if not isinstance(payload, (list, tuple)):
        rows = _get(payload, "top_genes")
        if rows is None:
            rows = _get(payload, "genes")
        if rows is None:
            return []
        payload = rows

    if not isinstance(payload, (list, tuple)):
        return []

    normalised: list[dict[str, Any]] = []
    for row in payload:
        if row is None:
            continue

        # A bare string is a legitimate gene list ("EPCAM", "KRAS", ...).
        if isinstance(row, str):
            symbol = _text(row)
            if symbol:
                normalised.append({"gene": symbol, "log2_fold_change": 0.0})
            continue

        symbol = _text(_first_key(row, _GENE_KEYS))
        if not symbol:
            continue

        # Preserve every upstream field so downstream consumers keep access to
        # p-values and percentages, then overwrite the two contract keys.
        if isinstance(row, Mapping):
            record = dict(row)
        elif hasattr(row, "to_dict"):
            try:
                record = dict(row.to_dict())
            except Exception:
                record = {}
        else:
            record = {}

        record["gene"] = symbol
        record["log2_fold_change"] = _number(_first_key(row, _FC_KEYS))
        normalised.append(record)

    return normalised


def extract_gene_symbols(gene_objects: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return de-duplicated uppercase gene symbols, preserving rank order.

    Rank order is meaningful — upstream modules truncate to ``max_genes``
    (gene annotation defaults to 10), so the strongest genes must come first.
    """

    symbols: list[str] = []
    seen: set[str] = set()
    for row in gene_objects or ():
        symbol = _text(_first_key(row, _GENE_KEYS)).upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def build_deg_bars(
    gene_objects: Sequence[Mapping[str, Any]],
    limit: int = 8,
) -> list[DegBar]:
    """Build TOP DEGs panel rows.

    Args:
        gene_objects: Normalised DEG rows.
        limit: Maximum rows. The current UI applies no cap of its own, so the
            producer owns it; 8 matches the existing pipeline.
    """

    bars: list[DegBar] = []
    for row in list(gene_objects or ())[:limit]:
        symbol = _text(_first_key(row, _GENE_KEYS))
        if not symbol:
            continue
        bars.append(DegBar(gene=symbol, log2fc=_number(_first_key(row, _FC_KEYS))))
    return bars


# -- Pathway enrichment -------------------------


def shorten_source(source: object) -> str:
    """Collapse a gene-set library name to a short pill tag.

    ``"GO_Biological_Process_2023"`` becomes ``"GO"`` and ``"KEGG_2021_Human"``
    becomes ``"KEGG"``. chat.js renders this in a small fixed-height pill and
    its own regex only handles colon-prefixed IDs, so an unshortened Enrichr
    library name overflows the element.
    """

    text = _text(source)
    if not text:
        return ""
    upper = text.upper()
    for prefix, tag in _SOURCE_TAGS:
        if upper.startswith(prefix):
            return tag
    # Unknown library: keep it short rather than let it overflow the pill.
    return text.split("_", 1)[0][:12]


def neg_log10(p_value: object) -> float:
    """Return ``-log10(p)`` clamped to a finite, chart-safe value."""

    value = _number(p_value, default=1.0)
    if value <= 0.0:
        value = _MIN_P_VALUE
    return round(-log10(max(value, _MIN_P_VALUE)), 1)


def iter_pathways(pathway_result: object) -> list[Any]:
    """Return the pathway rows from a ``PathwayResult``, a list, or ``None``."""

    if pathway_result is None:
        return []
    if isinstance(pathway_result, (list, tuple)):
        return list(pathway_result)
    rows = _get(pathway_result, "pathways")
    if isinstance(rows, (list, tuple)):
        return list(rows)
    return []


def build_pathway_bars(pathway_result: object, limit: int = 6) -> list[PathwayBar]:
    """Build ENRICHED PATHWAYS panel rows from any pathway payload.

    Accepts a ``PathwayResult``, the legacy ``enrich_pathways`` list of dicts
    (whose ``name`` is prefixed ``"{source} · {name}"``), or ``None``.
    """

    bars: list[PathwayBar] = []
    for entry in iter_pathways(pathway_result)[:limit]:
        name = _text(_get(entry, "name"))
        source = _get(entry, "source")

        # The legacy adapter folds the source into the display name with a
        # middle dot; split it back out so the pill and label stay separate.
        if not source and " · " in name:
            source, name = name.split(" · ", 1)

        # PathwayEntry exposes adjusted_p_value; the legacy dict exposes pvalue
        # (which is also the adjusted value).
        p_value = _get(entry, "adjusted_p_value")
        if p_value is None:
            p_value = _get(entry, "pvalue")

        count = _get(entry, "overlap_count")
        if count is None:
            count = _get(entry, "gene_count")
        if count is None:
            overlap = _get(entry, "overlap_genes") or _get(entry, "overlap") or ()
            try:
                count = len(overlap)
            except TypeError:
                count = 0

        if not name:
            continue

        bars.append(
            PathwayBar(
                source=shorten_source(source),
                name=_text(name),
                neg_log10p=neg_log10(p_value),
                gene_count=int(_number(count)),
            )
        )
    return bars


def pathway_names(pathway_result: object, limit: int = 6) -> list[str]:
    """Return bare pathway names, used to build the PubMed query."""

    names: list[str] = []
    for entry in iter_pathways(pathway_result)[:limit]:
        name = _text(_get(entry, "name"))
        if not name:
            continue
        # Strip any "{source} · " prefix the legacy adapter added.
        if " · " in name:
            name = name.split(" · ", 1)[1]
        names.append(name)
    return names


# -- PubMed -------------------------------


def iter_papers(pubmed_result: object) -> list[Any]:
    """Return paper rows from a ``PubMedResult``, a list of dicts, or ``None``."""

    if pubmed_result is None:
        return []
    if isinstance(pubmed_result, (list, tuple)):
        return list(pubmed_result)
    rows = _get(pubmed_result, "papers")
    if isinstance(rows, (list, tuple)):
        return list(rows)
    return []


def paper_text(paper: object, max_chars: int = 600) -> str:
    """Return the abstract text for one paper, however it is spelled.

    ``search_pubmed`` yields ``abstract``; the legacy ``retrieve_abstracts``
    yields ``snippet``. Reading only one of them silently loses the evidence.
    """

    text = _text(_get(paper, "abstract")) or _text(_get(paper, "snippet"))
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def build_citations(pubmed_result: object, limit: int = 3) -> list[Citation]:
    """Build 1-based citation records from retrieved papers.

    Only papers that actually came back this turn become citations —
    ``docs/rules.md`` section 4 forbids citing anything else.
    """

    citations: list[Citation] = []
    for index, paper in enumerate(iter_papers(pubmed_result)[:limit], start=1):
        year = _get(paper, "year")
        citations.append(
            Citation(
                id=index,
                pmid=_text(_get(paper, "pmid")),
                title=_text(_get(paper, "title")),
                journal=_text(_get(paper, "journal")),
                year=year if isinstance(year, int) else None,
            )
        )
    return citations


# -- Gene annotation ---------------------------


def iter_annotations(annotation_result: object) -> list[Any]:
    """Return annotation rows from a ``GeneAnnotationResult``, list, or ``None``."""

    if annotation_result is None:
        return []
    if isinstance(annotation_result, (list, tuple)):
        return list(annotation_result)
    rows = _get(annotation_result, "genes")
    if isinstance(rows, (list, tuple)):
        return list(rows)
    return []


def annotation_symbol(annotation: object) -> str:
    """Return the gene symbol for an annotation row.

    ``GeneAnnotation`` has no legacy aliases — ``annotation["symbol"]`` raises
    KeyError — so the canonical ``gene_symbol`` is tried first.
    """

    return _text(
        _get(annotation, "gene_symbol")
        or _get(annotation, "symbol")
        or _get(annotation, "query_symbol")
    )


def status_of(result: object) -> str:
    """Return an upstream envelope's ``status_message``, or ""."""

    return _text(_get(result, "status_message"))


# Public aliases. Other modules in the package read heterogeneous upstream
# objects too, and should not reach into this module's privates to do it.
get_field = _get
clean_text = _text
clean_number = _number


__all__ = [
    "annotation_symbol",
    "clean_number",
    "clean_text",
    "get_field",
    "build_citations",
    "build_deg_bars",
    "build_pathway_bars",
    "extract_gene_symbols",
    "iter_annotations",
    "iter_papers",
    "iter_pathways",
    "neg_log10",
    "normalize_gene_objects",
    "paper_text",
    "pathway_names",
    "shorten_source",
    "status_of",
]
