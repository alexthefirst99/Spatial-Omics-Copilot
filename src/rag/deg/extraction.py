"""
DEG Extraction Module
=====================
STATUS: Ranking is backed by a real two-sided Wilcoxon rank-sum test with tie
and continuity correction (T-008), Benjamini-Hochberg FDR correction over the
genes actually tested (T-009), and a detection-based candidate pre-filter that
runs before any test (T-010). Effect sizes remain log2 fold-change.

Read ``rag.deg.stats`` for exactly what the test does and does not establish,
and the normalization caveat below, before quoting any p-value.

NORMALIZATION CAVEAT — READ THIS
----------------
This module runs on ``adata.X`` exactly as stored in the uploaded ``.h5ad``.
It does NOT normalize, and it does not assume the values are raw counts; it
inspects them and reports what it observed in ``expression_source``.

``preprocess_adata`` (normalize_total + log1p) is only ever invoked inside
``run_spatial_clustering`` and never writes its result back to disk, so no
normalized matrix is available to this module today.

The consequence is material: **a Wilcoxon test on library-size-uncorrected
values is confounded by per-spot sequencing depth.** A spot with a higher total
count tends to rank higher for essentially every gene, so an ROI that happens
to contain deeper-sequenced spots will show apparent enrichment across the
board. This is the single largest caveat on this module's output — it can
manufacture a whole-transcriptome "signal" from a purely technical gradient.

The fix belongs upstream: T-034 (Person 1) should persist
``adata.layers["counts"]`` plus a normalized matrix so DEG can read a
depth-corrected layer. See ``docs/validation/person2_deg_notes.md``.

Opt-in normalization is available via ``normalize=True`` on ``compute_deg``,
defaulting to OFF so the ``log2_fold_change`` values already rendered in the UI
do not silently change.

Output contract
--------
``run_roi_deg`` returns a ``DEGResult`` (see ``rag.deg.models``).
``get_roi_high_expression_genes`` and ``get_cluster_high_expression_genes``
keep their original signatures and dict output, return ``None`` in exactly the
cases they did before, and gain ``pvalue`` / ``adj_pvalue`` / ``statistic``
additively. ``docs/specs.md`` section 3.1 remains satisfied.

By default the legacy wrappers do NOT filter by FDR — they preserve today's
pure log2FC ranking and set ``fdr_applied=False``, so a consumer can never
mistake a ranked list for a statistically significant one. Pass
``fdr_threshold=`` to opt in.

Error handling
-------
No public function in this module raises. Every failure path returns either
``None`` (legacy wrappers, matching current behaviour) or a ``DEGResult`` with
a populated ``status`` / ``status_message``. Status messages never contain
filesystem paths, usernames, or raw exception text; details go to the logger.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np
import scipy.sparse as sp

from rag.deg.coordinates import resolve_image_spatial_coordinates
from rag.deg.filtering import filter_deg_candidates_with_count
from rag.deg.geometry import (
    PolygonValidationError,
    build_roi_mask,
    validate_polygons,
)
from rag.deg.models import (
    MESSAGE_NO_DATA,
    SOURCE_EMPTY,
    SOURCE_RAW_COUNTS,
    SOURCE_UNKNOWN,
    STATUS_BARCODE_MISMATCH,
    STATUS_EMPTY_SELECTION,
    STATUS_ERROR,
    STATUS_INVALID_INPUT,
    STATUS_NO_DATA,
    STATUS_NO_SIGNIFICANT,
    STATUS_OK,
    DEGResult,
    GeneStat,
)
from rag.deg.stats import (
    DEFAULT_CHUNK_SIZE,
    REASON_NOT_REQUESTED,
    adjust_pvalues,
    wilcoxon_rank_sum,
)
from rag.deg.workspace import (
    PathResolver,
    WorkspacePathError,
    default_cluster_path_resolver,
    default_state_path_resolver,
    read_json,
    resolve_h5ad_path,
    resolve_workspace_file,
)

logger = logging.getLogger(__name__)

# Preserved from the original implementation so existing log2FC values are
# bit-for-bit unchanged.
_PSEUDOCOUNT = 1e-9

# Legacy candidate gate: a gene must be detected in at least 5% of selected
# spots to enter the ranked list.
_MIN_PCT_SELECTED = 0.05

DEFAULT_TOP_N = 25
DEFAULT_MIN_CELLS = 10
DEFAULT_FDR_THRESHOLD = 0.05

# Keep the RAG layer independent of app/config.py (docs/rules.md section 3).
FDR_THRESHOLD_ENV_VAR = "COPILOT_DEG_FDR_THRESHOLD"


def _configured_fdr_threshold() -> float:
    """Read the default FDR threshold from the environment.

    Returns:
        The configured threshold, or ``DEFAULT_FDR_THRESHOLD`` when the
        variable is unset, unparseable, or outside ``(0, 1]``.
    """

    raw = os.environ.get(FDR_THRESHOLD_ENV_VAR)
    if raw is None or not str(raw).strip():
        return DEFAULT_FDR_THRESHOLD
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("Ignoring unparseable %s.", FDR_THRESHOLD_ENV_VAR)
        return DEFAULT_FDR_THRESHOLD
    if not np.isfinite(value) or not 0.0 < value <= 1.0:
        logger.warning("Ignoring out-of-range %s.", FDR_THRESHOLD_ENV_VAR)
        return DEFAULT_FDR_THRESHOLD
    return value


def _observed_expression_source(matrix: Any) -> str:
    """Report what the stored values actually look like, without assuming.

    Args:
        matrix: Expression matrix, sparse or dense.

    Returns:
        ``SOURCE_RAW_COUNTS`` when every stored value is a finite integer,
        ``SOURCE_UNKNOWN`` when any value is fractional or non-finite, and
        ``SOURCE_EMPTY`` when there are no stored values at all.
    """

    values = matrix.data if sp.issparse(matrix) else np.asarray(matrix).ravel()
    if values.size == 0:
        return SOURCE_EMPTY

    # Chunked so the temporary comparison array stays bounded on a matrix with
    # tens of millions of stored values.
    step = 1_000_000
    for start in range(0, values.size, step):
        block = np.asarray(values[start : start + step], dtype=np.float64)
        if not np.all(np.isfinite(block)):
            return SOURCE_UNKNOWN
        if np.any(block != np.floor(block)):
            return SOURCE_UNKNOWN
    return SOURCE_RAW_COUNTS


def _group_mean_and_pct(
    matrix: Any,
    mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute per-gene mean and detection rate for one group of spots.

    Sparse-safe: no densification. Mirrors the original implementation exactly
    so existing ``mean_expression`` / ``pct_spots_expressed`` values do not
    shift.

    Args:
        matrix: Expression matrix, spots x genes.
        mask: Boolean row mask selecting the group.

    Returns:
        A tuple of (mean per gene, fraction of spots expressing per gene).
    """

    subset = matrix[mask]
    if sp.issparse(subset):
        mean = np.asarray(subset.mean(axis=0)).ravel()
        pct = np.asarray((subset > 0).mean(axis=0)).ravel()
    else:
        subset = np.asarray(subset)
        mean = subset.mean(axis=0)
        pct = (subset > 0).mean(axis=0)
    return mean, pct


def _two_group_mean_and_pct(
    matrix: Any,
    mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Compute selected and reference summaries without slicing the reference.

    On Visium HD data the reference commonly contains more than 100,000 rows.
    Creating ``matrix[~mask]`` copies most of a large CSR matrix and dominated
    the interactive DEG runtime. The reference sums and detection counts are
    instead derived by subtracting the selected summaries from one full-matrix
    pass.

    Args:
        matrix: Expression matrix, spots x genes.
        mask: Boolean row mask selecting the ROI or cluster.

    Returns:
        Selected mean, selected detection fraction, reference mean, and
        reference detection fraction, in that order.
    """

    selected_mask = np.asarray(mask, dtype=bool).ravel()
    n_selected = int(selected_mask.sum())
    n_reference = int(selected_mask.size - n_selected)
    n_genes = int(matrix.shape[1])

    if sp.issparse(matrix):
        working = matrix.tocsr()
        if not working.has_canonical_format:
            working = working.copy()
            working.sum_duplicates()

        selected = working[selected_mask]
        selected_sum = np.asarray(
            selected.sum(axis=0, dtype=np.float64)
        ).ravel()
        total_sum = np.asarray(
            working.sum(axis=0, dtype=np.float64)
        ).ravel()

        selected_positive = np.bincount(
            selected.indices[selected.data > 0],
            minlength=n_genes,
        ).astype(np.float64, copy=False)
        total_positive = np.bincount(
            working.indices[working.data > 0],
            minlength=n_genes,
        ).astype(np.float64, copy=False)
    else:
        dense = np.asarray(matrix)
        selected = dense[selected_mask]
        selected_sum = np.asarray(selected.sum(axis=0, dtype=np.float64)).ravel()
        total_sum = np.asarray(dense.sum(axis=0, dtype=np.float64)).ravel()
        selected_positive = np.count_nonzero(selected > 0, axis=0).astype(
            np.float64,
            copy=False,
        )
        total_positive = np.count_nonzero(dense > 0, axis=0).astype(
            np.float64,
            copy=False,
        )

    mean_selected = selected_sum / n_selected
    pct_selected = selected_positive / n_selected

    if n_reference > 0:
        mean_reference = (total_sum - selected_sum) / n_reference
        pct_reference = (total_positive - selected_positive) / n_reference
        # Floating point subtraction can leave tiny negative residuals when
        # the reference is all zero. These values are counts and means, so a
        # lower bound of zero is exact and prevents invalid fold changes.
        mean_reference = np.maximum(mean_reference, 0.0)
        pct_reference = np.maximum(pct_reference, 0.0)
    else:
        mean_reference = np.zeros(n_genes, dtype=np.float64)
        pct_reference = np.zeros(n_genes, dtype=np.float64)

    return mean_selected, pct_selected, mean_reference, pct_reference


def _normalize_matrix(matrix: Any) -> Any:
    """Library-size normalize to 1e4 per spot, then log1p (opt-in).

    OFF by default. Enabling this changes ``log2_fold_change`` relative to what
    the UI currently displays, which is why it is not the default — see the
    normalization caveat in the module docstring.

    Args:
        matrix: Expression matrix, spots x genes.

    Returns:
        A new normalized matrix; the input is not modified.
    """

    if sp.issparse(matrix):
        working = matrix.tocsr(copy=True).astype(np.float64)
        totals = np.asarray(working.sum(axis=1)).ravel()
        scale = np.divide(1e4, totals, out=np.zeros_like(totals), where=totals > 0)
        working = sp.diags(scale) @ working
        working.data = np.log1p(working.data)
        return working

    dense = np.asarray(matrix, dtype=np.float64)
    totals = dense.sum(axis=1, keepdims=True)
    scale = np.divide(1e4, totals, out=np.zeros_like(totals), where=totals > 0)
    return np.log1p(dense * scale)


def _empty_result(
    status: str,
    message: str,
    *,
    total_spots: int = 0,
    selected_spots: int = 0,
    reference_spots: int = 0,
    ranking_method: str = "",
) -> DEGResult:
    """Build a safe, empty ``DEGResult`` carrying a status message."""

    return DEGResult(
        selected_spots=selected_spots,
        reference_spots=reference_spots,
        total_spots=total_spots,
        ranking_method=ranking_method,
        top_genes=[],
        status=status,
        status_message=message,
    )


def compute_deg(
    adata: Any,
    selected: np.ndarray,
    *,
    top_n: int = DEFAULT_TOP_N,
    ranking_label: str = "roi_vs_non_roi_log2fc",
    min_cells: int = 0,
    fdr_threshold: float | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    normalize: bool = False,
    run_statistical_test: bool = True,
) -> DEGResult:
    """Run the full DEG engine over an in-memory AnnData.

    Order of operations: pre-filter (T-010) -> effect sizes -> rank-sum test
    (T-008) -> BH correction (T-009) -> ranking, optionally FDR-filtered.

    Args:
        adata: Spots x genes AnnData. Never mutated.
        selected: Boolean mask over spots marking the ROI / cluster.
        top_n: Maximum number of genes to return.
        ranking_label: Base label recorded in ``ranking_method``.
        min_cells: Detection pre-filter threshold. 0 disables filtering and is
            the default, so legacy callers see an unchanged gene set.
        fdr_threshold: When set, only genes with ``adj_pvalue`` below this are
            returned and ``fdr_applied`` is True. When None, the legacy log2FC
            ranking is used unfiltered.
        chunk_size: Gene block width for the rank-sum test.
        normalize: Opt-in library-size normalization; see module docstring.
        run_statistical_test: Run Wilcoxon and BH correction. Interactive
            callers that only display fold-change rankings can disable this
            expensive step. FDR filtering always enables the test because it
            cannot be computed without p-values.

    Returns:
        A populated ``DEGResult``.
    """

    total_spots = int(adata.n_obs)
    mask = np.asarray(selected, dtype=bool).ravel()
    selected_count = int(mask.sum())
    reference_count = total_spots - selected_count
    n_genes_input = int(adata.n_vars)

    if selected_count == 0:
        return _empty_result(
            STATUS_EMPTY_SELECTION,
            "The selected region contains no spots.",
            total_spots=total_spots,
            selected_spots=0,
            reference_spots=reference_count,
            ranking_method=ranking_label,
        )

    if n_genes_input == 0:
        return _empty_result(
            STATUS_NO_DATA,
            MESSAGE_NO_DATA,
            total_spots=total_spots,
            selected_spots=selected_count,
            reference_spots=reference_count,
            ranking_method=ranking_label,
        )

    # -- T-010: pre-filter before any test -----------------
    filtered, n_filtered_out = filter_deg_candidates_with_count(adata, min_cells)
    var_names = np.asarray(filtered.var_names, dtype=object)
    matrix = filtered.X
    if normalize:
        matrix = _normalize_matrix(matrix)
    expression_source = _observed_expression_source(matrix)

    n_candidates = int(var_names.size)
    if n_candidates == 0:
        return DEGResult(
            selected_spots=selected_count,
            reference_spots=reference_count,
            total_spots=total_spots,
            ranking_method=ranking_label,
            status=STATUS_NO_SIGNIFICANT,
            status_message=(
                "No genes passed the detection pre-filter, so no test was run."
            ),
            n_genes_input=n_genes_input,
            n_genes_filtered_out=n_filtered_out,
            expression_source=expression_source,
            min_cells=max(0, int(min_cells)),
            fdr_threshold=fdr_threshold,
            fdr_applied=fdr_threshold is not None,
        )

    # -- Effect sizes ---------------------------
    has_reference = reference_count > 0
    (
        mean_selected,
        pct_selected,
        mean_reference,
        pct_reference,
    ) = _two_group_mean_and_pct(matrix, mask)
    if has_reference:
        log2fc = np.log2(
            (mean_selected + _PSEUDOCOUNT) / (mean_reference + _PSEUDOCOUNT)
        )
        ranking_method = ranking_label
    else:
        log2fc = np.zeros_like(mean_selected)
        ranking_method = f"{ranking_label}_mean_expression_only_no_reference"

    # -- T-008 / T-009: test, then correct -----------------
    # The interactive UI ranks by fold change and does not display or filter
    # p-values. Densifying and ranking every spot for every gene can take
    # several minutes on a Visium HD matrix, so that caller uses the explicit
    # effect-size-only path below. Inferential callers retain the full test.
    fdr_applied = fdr_threshold is not None
    statistics_requested = bool(run_statistical_test) or fdr_applied
    if statistics_requested:
        statistic, pvalue, testable, reasons = wilcoxon_rank_sum(
            matrix,
            mask,
            chunk_size=chunk_size,
        )
        adj_pvalue, n_tested = adjust_pvalues(pvalue, testable)
    else:
        statistic = np.zeros(n_candidates, dtype=np.float64)
        pvalue = np.ones(n_candidates, dtype=np.float64)
        adj_pvalue = np.ones(n_candidates, dtype=np.float64)
        testable = np.zeros(n_candidates, dtype=bool)
        reasons = np.full(n_candidates, REASON_NOT_REQUESTED, dtype=object)
        n_tested = 0
        ranking_method = f"{ranking_method}_effect_size_only"
    n_untestable = int(n_candidates - n_tested)

    # -- Selection policy -------------------------
    if fdr_applied:
        threshold = float(fdr_threshold)
        keep = testable & (adj_pvalue < threshold) & (log2fc > 0)
        candidate_indices = np.flatnonzero(keep)
        # Most significant first; ties broken by larger effect size.
        order = np.lexsort(
            (-log2fc[candidate_indices], adj_pvalue[candidate_indices])
        )
        ranking_method = f"{ranking_method}_fdr{threshold:g}"
    elif has_reference:
        # Legacy gate, preserved exactly.
        candidate_mask = (
            (mean_selected > 0)
            & (pct_selected >= _MIN_PCT_SELECTED)
            & (log2fc > 0)
        )
        candidate_indices = np.flatnonzero(candidate_mask)
        if candidate_indices.size == 0:
            candidate_indices = np.flatnonzero((mean_selected > 0) & (log2fc > 0))
        if candidate_indices.size == 0:
            candidate_indices = np.flatnonzero(mean_selected > 0)
        order = np.lexsort(
            (-mean_selected[candidate_indices], -log2fc[candidate_indices])
        )
    else:
        candidate_indices = np.argsort(mean_selected)[::-1]
        order = np.arange(candidate_indices.size)

    top_indices = candidate_indices[order][: max(0, int(top_n))]

    genes = [
        GeneStat(
            gene=str(var_names[idx]),
            log2_fold_change=float(log2fc[idx]),
            mean_expression=float(mean_selected[idx]),
            pct_spots_expressed=float(pct_selected[idx]),
            mean_reference=float(mean_reference[idx]),
            pct_reference=float(pct_reference[idx]),
            pvalue=float(pvalue[idx]),
            adj_pvalue=float(adj_pvalue[idx]),
            statistic=float(statistic[idx]),
            testable=bool(testable[idx]),
            untestable_reason=str(reasons[idx]),
        )
        for idx in top_indices
    ]

    significance_cutoff = (
        float(fdr_threshold) if fdr_applied else DEFAULT_FDR_THRESHOLD
    )
    n_significant = int(
        np.count_nonzero(testable & (adj_pvalue < significance_cutoff))
    )

    if genes:
        status = STATUS_OK
        if fdr_applied:
            message = (
                f"{len(genes)} gene(s) significant at FDR < "
                f"{float(fdr_threshold):g} out of {n_tested} tested."
            )
        else:
            if statistics_requested:
                message = (
                    f"Ranked {len(genes)} gene(s) by fold change from "
                    f"{n_tested} tested; no FDR filter was applied."
                )
            else:
                message = (
                    f"Ranked {len(genes)} gene(s) by fold change; statistical "
                    "testing was skipped for interactive performance."
                )
    else:
        status = STATUS_NO_SIGNIFICANT
        message = (
            "Analysis ran, but no gene passed the significance threshold."
            if fdr_applied
            else "Analysis ran, but no gene was enriched in the selected region."
        )

    return DEGResult(
        selected_spots=selected_count,
        reference_spots=reference_count,
        total_spots=total_spots,
        ranking_method=ranking_method,
        top_genes=genes,
        status=status,
        status_message=message,
        n_genes_input=n_genes_input,
        n_genes_tested=n_tested,
        n_genes_filtered_out=n_filtered_out,
        n_genes_untestable=n_untestable,
        n_significant=n_significant,
        fdr_applied=fdr_applied,
        fdr_threshold=fdr_threshold,
        expression_source=expression_source,
        min_cells=max(0, int(min_cells)),
    )


def _rank_high_expression_genes(
    adata: Any,
    selected: np.ndarray,
    top_n: int,
    ranking_label: str,
    *,
    min_cells: int = 0,
    fdr_threshold: float | None = None,
) -> dict[str, Any]:
    """Legacy engine entry point, preserved for backward compatibility.

    Kept because ``src/tests/test_deg.py`` and any downstream caller may invoke
    it directly. Output is the original dict shape plus the new statistical
    fields; ranking behaviour under the default arguments is unchanged.

    Args:
        adata: Spots x genes AnnData.
        selected: Boolean spot mask.
        top_n: Maximum genes to return.
        ranking_label: Base label for ``ranking_method``.
        min_cells: Detection pre-filter; 0 (default) preserves legacy output.
        fdr_threshold: Opt-in FDR filtering; None preserves legacy output.

    Returns:
        The DEG result dict.
    """

    return compute_deg(
        adata,
        selected,
        top_n=top_n,
        ranking_label=ranking_label,
        min_cells=min_cells,
        fdr_threshold=fdr_threshold,
    ).to_dict()


def _read_adata(path: str) -> Any:
    """Load an AnnData file, converting every failure into a safe error.

    ``ad.read_h5ad`` was previously uncaught, so a truncated or corrupt upload
    propagated out of the RAG layer.

    Args:
        path: Path to a ``.h5ad`` file.

    Returns:
        The loaded AnnData.

    Raises:
        WorkspacePathError: If the file cannot be read or parsed.
    """

    import anndata as ad

    try:
        return ad.read_h5ad(path)
    except (OSError, KeyError, ValueError, TypeError) as exc:
        logger.warning("Could not read h5ad at %s: %s", path, exc)
        raise WorkspacePathError("Gene expression file could not be read.") from exc
    except Exception as exc:
        # Documented outermost catch-all: h5py/anndata raise a wide range of
        # third-party exception types on corrupt input, and none may escape.
        logger.exception("Unexpected failure reading h5ad at %s", path)
        raise WorkspacePathError("Gene expression file could not be read.") from exc


def _resolve_selection_mask(adata: Any, roi_selection: Any) -> np.ndarray:
    """Turn a caller-supplied selection into a boolean spot mask.

    Accepts a boolean mask, a mapping with a ``coords`` key, or a sequence of
    polygon rings.

    Args:
        adata: The loaded AnnData, used for spot count and coordinates.
        roi_selection: Selection specification.

    Returns:
        A boolean mask of length ``adata.n_obs``.

    Raises:
        PolygonValidationError: If the selection cannot be interpreted.
    """

    n_obs = int(adata.n_obs)

    if isinstance(roi_selection, np.ndarray) and roi_selection.dtype == bool:
        if roi_selection.size != n_obs:
            raise PolygonValidationError(
                "Selection mask length does not match the number of spots."
            )
        return roi_selection

    coords = roi_selection
    if isinstance(roi_selection, dict):
        coords = roi_selection.get("coords")

    if coords is None:
        raise PolygonValidationError("No ROI selection was provided.")

    polygons = validate_polygons(coords)
    if "spatial" not in adata.obsm:
        raise PolygonValidationError("Dataset has no spatial coordinates.")
    return build_roi_mask(np.asarray(adata.obsm["spatial"]), polygons)


def run_roi_deg(
    adata_path: str,
    roi_selection: Any,
    config: dict[str, Any] | None = None,
) -> DEGResult:
    """Primary DEG entry point (T-008 / T-009 / T-010 / T-044).

    Unlike the legacy wrappers this applies FDR filtering by default, and it
    always returns a ``DEGResult`` — including for the "nothing loaded" case,
    which carries the exact message ``"No gene expression data loaded."`` so a
    caller can distinguish it from "analysis ran and found nothing".

    Args:
        adata_path: Path to the ``.h5ad`` file.
        roi_selection: Boolean spot mask, a sequence of polygon rings, or a
            mapping with a ``coords`` key.
        config: Optional overrides — ``top_n``, ``min_cells``,
            ``fdr_threshold``, ``chunk_size``, ``normalize``, ``ranking_label``.

    Returns:
        A populated ``DEGResult``. Never raises.
    """

    settings = dict(config or {})
    try:
        top_n = int(settings.get("top_n", DEFAULT_TOP_N))
        min_cells = int(settings.get("min_cells", DEFAULT_MIN_CELLS))
        chunk_size = int(settings.get("chunk_size", DEFAULT_CHUNK_SIZE))
        normalize = bool(settings.get("normalize", False))
        run_statistical_test = bool(settings.get("run_statistical_test", True))
        ranking_label = str(settings.get("ranking_label", "roi_vs_non_roi_log2fc"))
        threshold = settings.get("fdr_threshold", _configured_fdr_threshold())
        if threshold is not None:
            threshold = float(threshold)
    except (TypeError, ValueError):
        return _empty_result(
            STATUS_INVALID_INPUT,
            "Analysis configuration is invalid.",
        )

    if not isinstance(adata_path, str) or not adata_path.strip():
        return _empty_result(STATUS_NO_DATA, MESSAGE_NO_DATA)
    if "\x00" in adata_path or not os.path.exists(adata_path):
        return _empty_result(STATUS_NO_DATA, MESSAGE_NO_DATA)

    try:
        adata = _read_adata(adata_path)
    except WorkspacePathError as exc:
        # Treat unreadable expression files as unavailable and log the cause.
        logger.info("Treating unreadable h5ad as no-data: %s", exc)
        return _empty_result(STATUS_NO_DATA, MESSAGE_NO_DATA)

    try:
        mask = _resolve_selection_mask(adata, roi_selection)
    except PolygonValidationError as exc:
        return _empty_result(
            STATUS_INVALID_INPUT,
            str(exc),
            total_spots=int(adata.n_obs),
        )

    try:
        return compute_deg(
            adata,
            mask,
            top_n=top_n,
            ranking_label=ranking_label,
            min_cells=min_cells,
            fdr_threshold=threshold,
            chunk_size=chunk_size,
            normalize=normalize,
            run_statistical_test=run_statistical_test,
        )
    except Exception:
        # Documented outermost catch-all — docs/rules.md section 4 forbids an
        # unhandled exception escaping a RAG tool.
        logger.exception("DEG computation failed for %s", adata_path)
        return _empty_result(
            STATUS_ERROR,
            "Differential expression analysis could not be completed.",
            total_spots=int(adata.n_obs),
        )


def get_roi_high_expression_genes(
    work_dir: str,
    coords: Any,
    folder_id: str = "",
    top_n: int = DEFAULT_TOP_N,
    *,
    fdr_threshold: float | None = None,
    min_cells: int = 0,
    state_path_resolver: PathResolver | None = None,
) -> dict[str, Any] | None:
    """Return genes enriched in selected ROI spots compared with non-ROI spots.

    Signature and return shape are unchanged from the original implementation.
    ``None`` is still returned when no ROI, no workspace state, or no h5ad is
    available, so ``app.py``'s empty-state card keeps rendering.

    Args:
        work_dir: Workspace root directory.
        coords: List of polygon coordinate rings. Treated as untrusted input.
        folder_id: User folder id; validated against path traversal.
        top_n: Maximum genes to return.
        fdr_threshold: Opt-in FDR filtering. None (default) preserves today's
            unfiltered log2FC ranking and reports ``fdr_applied=False``.
        min_cells: Detection pre-filter; 0 (default) preserves today's gene set.
        state_path_resolver: Optional override for workspace state resolution.

    Returns:
        The DEG result dict, or None when no analysis was possible.
    """

    resolver = state_path_resolver or default_state_path_resolver
    try:
        if not coords:
            return None
        state_path = resolve_workspace_file(work_dir, folder_id, resolver)
        if not os.path.exists(state_path):
            return None
        state = read_json(state_path)
        h5ad_path = resolve_h5ad_path(str(work_dir), state)
        adata = _read_adata(h5ad_path)
    except WorkspacePathError as exc:
        logger.info("ROI DEG unavailable: %s", exc)
        return None
    except Exception:
        logger.exception("Unexpected failure preparing ROI DEG")
        return None

    try:
        if "spatial" not in adata.obsm:
            return None
        polygons = validate_polygons(coords)
        roi_bounds = (
            min(polygon.bounds[0] for polygon in polygons),
            min(polygon.bounds[1] for polygon in polygons),
            max(polygon.bounds[2] for polygon in polygons),
            max(polygon.bounds[3] for polygon in polygons),
        )
        image_size = None
        args_path = os.path.join(os.path.dirname(state_path), "args.json")
        if os.path.exists(args_path):
            try:
                image_size = read_json(args_path).get("heightWidth")
            except Exception:
                logger.info("Could not read image dimensions for ROI coordinate alignment")

        resolution = resolve_image_spatial_coordinates(
            adata,
            image_size=image_size,
            roi_bounds=roi_bounds,
        )
        if resolution.source != 'obsm["spatial"]':
            logger.info("Using %s coordinates for ROI selection", resolution.source)
        mask = build_roi_mask(resolution.coordinates, polygons)
    except PolygonValidationError as exc:
        logger.info("Rejected ROI polygon: %s", exc)
        return None
    except Exception:
        logger.exception("Unexpected failure building ROI mask")
        return None

    try:
        return compute_deg(
            adata,
            mask,
            top_n=top_n,
            ranking_label="roi_vs_non_roi_log2fc",
            min_cells=min_cells,
            fdr_threshold=fdr_threshold,
            run_statistical_test=fdr_threshold is not None,
        ).to_dict()
    except Exception:
        logger.exception("ROI DEG computation failed")
        return None


def get_cluster_high_expression_genes(
    work_dir: str,
    cluster_id: Any,
    folder_id: str = "",
    top_n: int = DEFAULT_TOP_N,
    *,
    fdr_threshold: float | None = None,
    min_cells: int = 0,
    state_path_resolver: PathResolver | None = None,
    cluster_path_resolver: PathResolver | None = None,
) -> dict[str, Any] | None:
    """Return genes enriched in one spatial cluster compared with all others.

    Signature and return shape are unchanged from the original implementation,
    including the ``cluster_id`` and ``cluster_key`` keys.

    Args:
        work_dir: Workspace root directory.
        cluster_id: Cluster label to compare against every other spot.
        folder_id: User folder id; validated against path traversal.
        top_n: Maximum genes to return.
        fdr_threshold: Opt-in FDR filtering; None preserves legacy ranking.
        min_cells: Detection pre-filter; 0 preserves today's gene set.
        state_path_resolver: Optional override for state path resolution.
        cluster_path_resolver: Optional override for cluster path resolution.

    Returns:
        The DEG result dict, or None when no analysis was possible.
    """

    state_resolver = state_path_resolver or default_state_path_resolver
    cluster_resolver = cluster_path_resolver or default_cluster_path_resolver
    try:
        if cluster_id is None:
            return None
        state_path = resolve_workspace_file(work_dir, folder_id, state_resolver)
        cluster_path = resolve_workspace_file(work_dir, folder_id, cluster_resolver)
        if not os.path.exists(state_path) or not os.path.exists(cluster_path):
            return None

        state = read_json(state_path)
        cluster_state = read_json(cluster_path)
        h5ad_path = resolve_h5ad_path(str(work_dir), state)
        adata = _read_adata(h5ad_path)
    except WorkspacePathError as exc:
        logger.info("Cluster DEG unavailable: %s", exc)
        return None
    except Exception:
        logger.exception("Unexpected failure preparing cluster DEG")
        return None

    try:
        clusters = cluster_state.get("clusters", {}) or {}
        if not isinstance(clusters, dict):
            return None
        wanted = str(cluster_id)
        spot_names = [str(name) for name in adata.obs_names]
        mask = np.array(
            [str(clusters.get(name)) == wanted for name in spot_names],
            dtype=bool,
        )

        # Distinguish mismatched barcodes from a valid empty cluster.
        if spot_names and not any(name in clusters for name in spot_names):
            logger.info(
                "Cluster assignment shares no barcode with the dataset "
                "(%d spots, %d assignments).",
                len(spot_names),
                len(clusters),
            )
            mismatch = _empty_result(
                STATUS_BARCODE_MISMATCH,
                "The cluster assignment does not match this dataset's spots.",
                total_spots=int(adata.n_obs),
                reference_spots=int(adata.n_obs),
                ranking_method="cluster_vs_non_cluster_log2fc",
            )
            mismatch.cluster_id = wanted
            mismatch.cluster_key = str(
                cluster_state.get("cluster_key", "spatial_cluster")
            )
            return mismatch.to_dict()

        result = compute_deg(
            adata,
            mask,
            top_n=top_n,
            ranking_label="cluster_vs_non_cluster_log2fc",
            min_cells=min_cells,
            fdr_threshold=fdr_threshold,
            run_statistical_test=fdr_threshold is not None,
        )
        result.cluster_id = wanted
        result.cluster_key = str(cluster_state.get("cluster_key", "spatial_cluster"))
        return result.to_dict()
    except Exception:
        logger.exception("Cluster DEG computation failed")
        return None


__all__ = [
    "DEFAULT_FDR_THRESHOLD",
    "DEFAULT_MIN_CELLS",
    "DEFAULT_TOP_N",
    "compute_deg",
    "get_cluster_high_expression_genes",
    "get_roi_high_expression_genes",
    "run_roi_deg",
]
