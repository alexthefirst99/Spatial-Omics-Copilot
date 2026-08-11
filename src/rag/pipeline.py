"""
RAG Pipeline
=================================
Two things live here:

1. ``_run_sequential`` — the original fallback pipeline used by the deprecated
   ``rag.agent`` shim before T-021. Nothing imports it anymore (the real
   LangGraph agent in ``rag.copilot_agent`` replaced it); it is kept only
   because ``src/tests/test_pipeline.py`` still exercises it directly.

2. ``run_integration_pipeline`` — the real, currently-wired integration chain
   (T-029 / T-042 / T-045 / T-052). It runs one ROI end to end: preprocess →
   cluster → resolve ROI to spot barcodes → crop the ROI image → DEG →
   gene annotation → pathway enrichment → PubMed → the copilot agent. Each
   step's real module is called directly (Rodney's ``rag.deg``, Quynh's
   ``rag.gene_annotation`` / ``rag.pathway_enrichment``, Anh's
   ``rag.pubmed_retrieval``, JN's ``rag.copilot_agent``) — nothing here
   reimplements their logic, per ``docs/rules.md`` section 1.
"""

from __future__ import annotations
import math
import os
from typing import Any, Optional

import numpy as np

from rag.pathway import enrich_pathways
from rag.pubmed import retrieve_abstracts
from rag.agent.prompt import build_prompt_context

from rag.contracts import ROIImageResult, ROISelection
from rag.preprocessing import preprocess_h5ad
from rag.clustering import cluster_adata
from rag.deg import run_roi_deg
from rag.deg.geometry import PolygonValidationError, build_roi_mask, validate_polygons
from rag.gene_annotation import run_gene_annotation_retrieval
from rag.pathway_enrichment import run_pathway_enrichment
from rag.pubmed_retrieval import build_pubmed_query, search_pubmed
from rag.copilot_agent import run_copilot_agent
import niceview.utils.io as vio


# Demo fallback when no h5ad is loaded (mixed brain cell-type profile)
_DEMO_GENE_OBJECTS = [
    {"gene": "SNAP25",  "log2_fold_change": 3.81},
    {"gene": "SYP",     "log2_fold_change": 3.44},
    {"gene": "SYT1",    "log2_fold_change": 3.12},
    {"gene": "GRIA1",   "log2_fold_change": 2.94},
    {"gene": "AIF1",    "log2_fold_change": 2.71},
    {"gene": "TREM2",   "log2_fold_change": 2.55},
    {"gene": "GFAP",    "log2_fold_change": 2.38},
    {"gene": "MBP",     "log2_fold_change": 2.20},
    {"gene": "C1QA",    "log2_fold_change": 2.05},
    {"gene": "MAPK1",   "log2_fold_change": 1.92},
    {"gene": "SPP1",    "log2_fold_change": 1.80},
    {"gene": "OLIG2",   "log2_fold_change": 1.68},
]


def _run_sequential(
    gene_objects: list,
    message: str = "",
    label: str = "selection",
    n_pathways: int = 6,
    n_abstracts: int = 3,
) -> dict:
    # Use demo fallback if no genes provided
    if not gene_objects:
        gene_objects = _DEMO_GENE_OBJECTS
        label = "demo"

    genes = [g["gene"] for g in gene_objects]

    # -- 1. Pathway Enrichment --
    pathways = enrich_pathways(genes, top_n=n_pathways)

    # -- 2. PubMed Retrieval --
    pathway_names = [p["name"] for p in pathways]
    abstracts = retrieve_abstracts(genes, pathways=pathway_names, n=n_abstracts)

    # -- 3. LLM Context --
    context_str = build_prompt_context(genes, pathways, abstracts, label=label)

    # -- Build UI metadata --
    trace = [
        {
            "step": "Extracted top DEGs",
            "detail": f"{len(genes)} genes · {label}",
            "icon": "deg",
        },
        {
            "step": "Pathway enrichment",
            "detail": "GO · KEGG",
            "icon": "pathway",
        },
        {
            "step": f"Retrieved {len(abstracts)} PubMed abstract{'s' if len(abstracts) != 1 else ''}",
            "detail": "",
            "icon": "pubmed",
        },
    ]

    pathway_bars = []
    for p in pathways:
        neg_log10p = round(-math.log10(max(p["pvalue"], 1e-15)), 1)
        source, short_name = (p["name"].split(" · ", 1) if " · " in p["name"] else ("", p["name"]))
        pathway_bars.append({
            "source": source,
            "name": short_name,
            "gene_count": p["gene_count"],
            "neg_log10p": neg_log10p,
        })

    degs = [
        {"gene": g["gene"], "log2fc": round(g["log2_fold_change"], 2)}
        for g in gene_objects[:8]
    ]

    citations = [
        {"id": i + 1, "pmid": ab["pmid"], "title": ab["title"],
         "journal": ab["journal"], "year": ab["year"]}
        for i, ab in enumerate(abstracts)
    ]

    return {
        "gene_objects": gene_objects,
        "context_str": context_str,
        "metadata": {
            "trace": trace,
            "degs": degs,
            "pathways": pathway_bars,
            "citations": citations,
            "label": label,
        },
    }


# --------------------------------------
# Real integration pipeline (T-029, T-042, T-045, T-052)
# --------------------------------------


def _field(source: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` off a dict/mapping or an attribute-style object."""

    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def map_roi_to_spatial_barcodes(
    adata_path: str,
    roi_selection: Any,
    config: dict | None = None,
) -> ROISelection:
    """Resolve an ROI (polygon or cluster) to concrete spot barcodes (T-052).

    Args:
        adata_path: Path to the clustered ``.h5ad`` file
            (``cluster_result["adata_path"]``), used for ``obs_names`` and, for
            polygon selections, ``obsm["spatial"]``.
        roi_selection: A ``ROISelection`` or ROISelection-shaped dict/mapping
            from the UI. ``selection_type`` must be ``"polygon"`` (using
            ``polygon_points``) or ``"cluster"`` (using ``cluster_id``).
        config: Optional overrides. Cluster-based selections require
            ``config["cluster_path"]`` — the JSON written by
            ``cluster_adata()`` / ``run_spatial_clustering()`` — since cluster
            labels are not written back into the ``.h5ad`` file.

    Returns:
        A new ``ROISelection`` with ``spot_ids``/``barcode_ids`` populated.
        Never raises — any failure comes back as an empty selection with
        ``status_message`` explaining why.
    """

    config = config or {}
    roi_id = _field(roi_selection, "roi_id", "") or ""
    selection_type = _field(roi_selection, "selection_type", "") or ""
    polygon_points = _field(roi_selection, "polygon_points")
    cluster_id = _field(roi_selection, "cluster_id")

    def _empty(status_message: str) -> ROISelection:
        return ROISelection(
            roi_id=roi_id,
            selection_type=selection_type,
            polygon_points=polygon_points,
            cluster_id=cluster_id,
            status_message=status_message,
        )

    if not isinstance(adata_path, str) or not adata_path.strip() or not vio.exists(adata_path):
        return _empty("No gene expression data loaded.")

    import anndata as ad

    try:
        adata = ad.read_h5ad(adata_path, backed="r")
    except Exception as exc:  # noqa: BLE001 - never raise out of a RAG tool
        return _empty(f"Could not read AnnData file: {exc}")

    try:
        obs_names = np.asarray([str(name) for name in adata.obs_names])

        if selection_type == "cluster":
            cluster_path = config.get("cluster_path")
            if not cluster_path or not vio.exists(cluster_path):
                return _empty("No cluster assignments available for cluster-based selection.")
            payload = vio.load_json(cluster_path)
            clusters = (payload.get("clusters") if isinstance(payload, dict) else None) or {}
            target = str(cluster_id)
            mask = np.array(
                [str(clusters.get(name)) == target for name in obs_names], dtype=bool
            )
        elif selection_type == "polygon":
            if "spatial" not in adata.obsm:
                return _empty("Dataset has no spatial coordinates.")
            try:
                polygons = validate_polygons(polygon_points)
            except PolygonValidationError as exc:
                return _empty(str(exc))
            mask = build_roi_mask(np.asarray(adata.obsm["spatial"]), polygons)
        else:
            return _empty(f"Unknown selection_type: {selection_type!r}")
    finally:
        if getattr(adata, "file", None) is not None:
            adata.file.close()

    barcodes = obs_names[mask].tolist()
    if not barcodes:
        return _empty("No spots found inside the selected region.")

    return ROISelection(
        roi_id=roi_id,
        selection_type=selection_type,
        polygon_points=polygon_points,
        cluster_id=cluster_id,
        spot_ids=list(barcodes),
        barcode_ids=list(barcodes),
        status_message="",
    )


def _polygon_bounds(polygon_points: Any) -> tuple[int, int, int, int] | None:
    """Return an integer (x_min, y_min, x_max, y_max) bounding box, or None."""

    points: list[list[float]] = []
    for ring in polygon_points or []:
        for vertex in ring or []:
            if len(vertex) >= 2:
                points.append([float(vertex[0]), float(vertex[1])])
    if not points:
        return None
    arr = np.asarray(points, dtype=np.float64)
    x_min, y_min = arr.min(axis=0)
    x_max, y_max = arr.max(axis=0)
    if not (np.isfinite([x_min, y_min, x_max, y_max]).all()):
        return None
    return int(x_min), int(y_min), int(x_max), int(y_max)


def prepare_roi_image_for_llm(
    image_path: str,
    roi_selection: Any,
    config: dict | None = None,
) -> ROIImageResult:
    """Crop the selected ROI out of the whole-slide image for a vision LLM (T-045).

    Only polygon selections have a crop-able bounding box; a cluster
    selection's spots are typically scattered across the tissue, so it
    returns a status message instead of a (misleading) crop of the cluster's
    bounding box.

    Args:
        image_path: Path to the whole-slide image (large TIFF/BigTIFF/SVS).
        roi_selection: A ``ROISelection`` (or dict) with ``polygon_points``.
        config: Optional overrides — ``output_dir`` (default
            ``tmp_data/roi_crops``), ``image_format`` (``"png"`` or
            ``"jpeg"``), ``max_dimension`` (longest crop side before
            downscaling, default 1536px).

    Returns:
        A ``ROIImageResult``. Never raises — failures come back with an empty
        ``crop_path`` and a ``status_message``.
    """

    config = config or {}
    roi_id = _field(roi_selection, "roi_id", "") or ""

    def _empty(status_message: str) -> ROIImageResult:
        return ROIImageResult(roi_id=roi_id, status_message=status_message)

    if not isinstance(image_path, str) or not image_path.strip() or not os.path.exists(image_path):
        return _empty("No whole-slide image loaded.")

    polygon_points = _field(roi_selection, "polygon_points")
    bounds = _polygon_bounds(polygon_points)
    if bounds is None:
        return _empty("ROI image cropping requires a drawn polygon selection.")
    x_min, y_min, x_max, y_max = bounds

    crop = None
    try:
        import rasterio
        from rasterio.windows import Window

        with rasterio.open(image_path) as src:
            x0 = max(0, min(x_min, src.width))
            y0 = max(0, min(y_min, src.height))
            x1 = max(0, min(x_max, src.width))
            y1 = max(0, min(y_max, src.height))
            if x1 > x0 and y1 > y0:
                window = Window(x0, y0, x1 - x0, y1 - y0)
                crop = src.read(window=window)
                crop = np.moveaxis(crop, 0, -1)
                if crop.shape[2] == 1:
                    crop = crop[:, :, 0]
    except Exception as exc:  # noqa: BLE001 - fall through to tifffile/PIL below
        crop = None
        _rasterio_error = exc
    else:
        _rasterio_error = None

    if crop is None:
        try:
            import tifffile

            with tifffile.TiffFile(image_path) as tif:
                page = tif.pages[0]
                ih, iw = page.shape[0], page.shape[1]
                x1 = min(x_max, iw)
                y1 = min(y_max, ih)
                if x1 > x_min and y1 > y_min:
                    crop = page.asarray()[y_min:y1, x_min:x1]
        except Exception:
            crop = None

    if crop is None or crop.size == 0:
        detail = f" ({_rasterio_error})" if _rasterio_error else ""
        return _empty(f"Could not crop the ROI from the whole-slide image{detail}.")

    from PIL import Image as PILImage

    PILImage.MAX_IMAGE_PIXELS = None
    if crop.ndim == 3 and crop.shape[2] == 4:
        crop = crop[:, :, :3]
    image = PILImage.fromarray(crop)

    max_dimension = int(config.get("max_dimension", 1536))
    scale_factor = 1.0
    if max(image.width, image.height) > max_dimension > 0:
        scale_factor = max_dimension / float(max(image.width, image.height))
        new_size = (max(1, round(image.width * scale_factor)), max(1, round(image.height * scale_factor)))
        image = image.resize(new_size, PILImage.LANCZOS)

    image_format = str(config.get("image_format", "png")).lower()
    ext = "jpg" if image_format in ("jpg", "jpeg") else "png"
    output_dir = config.get("output_dir", os.path.join("tmp_data", "roi_crops"))
    os.makedirs(output_dir, exist_ok=True)
    crop_name = f"roi_{roi_id or 'selection'}_{x_min}_{y_min}_{x_max}_{y_max}.{ext}"
    crop_path = os.path.join(output_dir, crop_name)

    try:
        image.save(crop_path)
    except Exception as exc:  # noqa: BLE001 - never raise out of a RAG tool
        return _empty(f"Could not save the ROI crop: {exc}")

    return ROIImageResult(
        roi_id=roi_id,
        crop_path=crop_path,
        width=image.width,
        height=image.height,
        image_format=ext,
        scale_factor=scale_factor,
        status_message="",
    )


def run_integration_pipeline(
    *,
    h5ad_path: str,
    image_path: str | None,
    roi_selection: Any,
    question: str = "",
    config: dict | None = None,
) -> dict:
    """Run one ROI end to end through every module's real implementation (T-029).

    This is the chain the task plan describes for the integration pipeline:
    preprocess -> cluster -> resolve ROI -> crop ROI image -> DEG ->
    gene annotation -> pathway enrichment -> PubMed -> the copilot agent.
    Each step calls the owning person's real public function; nothing here
    reimplements pathway/PubMed/agent logic.

    Args:
        h5ad_path: Path to the uploaded ``.h5ad`` file.
        image_path: Path to the whole-slide image, or ``None`` to skip the
            ROI image crop (the agent still runs, just without an image).
        roi_selection: A ``ROISelection`` or ROISelection-shaped dict
            describing the user's drawn polygon or clicked cluster.
        question: The researcher's chat message.
        config: Shared config forwarded to every step.

    Returns:
        A dict with every intermediate result (``preprocess``, ``cluster``,
        ``roi``, ``roi_image``, ``deg``, ``gene_annotations``, ``pathways``,
        ``pubmed``) plus the ``{gene_objects, context_str, metadata}`` legacy
        shape ``app/routes.py`` expects, and the richer ``answer``/``trace``/
        ``citations`` fields for callers that want the full ``AgentResult``.
    """

    config = dict(config or {})

    preprocess_payload = preprocess_h5ad(h5ad_path, config)
    preprocessed_path = preprocess_payload["adata_path"]

    cluster_payload = cluster_adata(preprocessed_path, config)
    clustered_path = cluster_payload["adata_path"]
    cluster_path = cluster_payload["cluster_path"]

    resolved_roi = map_roi_to_spatial_barcodes(
        clustered_path, roi_selection, {**config, "cluster_path": cluster_path}
    )

    roi_image_result = None
    if image_path:
        roi_image_result = prepare_roi_image_for_llm(image_path, resolved_roi, config)

    # run_roi_deg needs a mask aligned to the clustered AnnData observations.
    deg_selection: Any = resolved_roi
    if resolved_roi.barcode_ids:
        import anndata as ad

        deg_adata = ad.read_h5ad(clustered_path, backed="r")
        try:
            obs_names = np.asarray([str(name) for name in deg_adata.obs_names])
        finally:
            if getattr(deg_adata, "file", None) is not None:
                deg_adata.file.close()
        deg_selection = np.isin(obs_names, resolved_roi.barcode_ids)

    deg_result = run_roi_deg(clustered_path, deg_selection, config)
    genes = [gene["gene"] for gene in deg_result.to_dict()["top_genes"]]

    gene_annotation_result = run_gene_annotation_retrieval(genes, config) if genes else None
    pathway_result = run_pathway_enrichment(genes, config) if genes else None

    pathway_names = (
        [p["name"] for p in pathway_result.to_dict()["pathways"]] if pathway_result else []
    )
    disease = config.get("disease", "colorectal cancer")
    pubmed_result = None
    if genes:
        pubmed_query = build_pubmed_query(genes, pathway_names, disease=disease)
        pubmed_result = search_pubmed(
            pubmed_query, max_results=int(config.get("pubmed_max_results", 5))
        )

    agent_result = run_copilot_agent(
        question=question,
        roi=resolved_roi,
        roi_image=roi_image_result,
        deg=deg_result,
        gene_annotations=gene_annotation_result,
        pathways=pathway_result,
        pubmed=pubmed_result,
        config=config,
        label=resolved_roi.selection_type or "selection",
        disease=disease,
    )

    result = {
        "preprocess": preprocess_payload,
        "cluster": cluster_payload,
        "roi": resolved_roi.to_dict(),
        "roi_image": roi_image_result.to_dict() if roi_image_result else None,
        "deg": deg_result.to_dict(),
        "gene_annotations": gene_annotation_result.to_dict() if gene_annotation_result else None,
        "pathways": pathway_result.to_dict() if pathway_result else None,
        "pubmed": pubmed_result.to_dict() if pubmed_result else None,
        "answer": agent_result.answer,
    }
    result.update(agent_result.to_legacy_dict())
    return result
