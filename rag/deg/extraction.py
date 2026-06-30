"""
DEG Extraction Module
=====================
CURRENTLY PARTIAL — works but uses only log2 fold-change ranking with no
statistical testing. No p-values, no multiple testing correction.
Improve _rank_high_expression_genes() with Wilcoxon test + BH correction.

Input (get_cluster_high_expression_genes):
    work_dir   : str           — session working directory
    cluster_id : str           — cluster label, e.g. "2"
    folder_id  : str           — user folder ID (default "")
    top_n      : int           — number of top genes to return (default 25)

Input (get_roi_high_expression_genes):
    work_dir   : str           — session working directory
    coords     : list          — list of polygon coordinate lists
    folder_id  : str           — user folder ID (default "")
    top_n      : int           — number of top genes to return (default 25)

Expected output — dict:
    {
        "selected_spots":  120,   # number of spots in cluster/ROI
        "reference_spots": 880,   # number of spots outside
        "total_spots":     1000,  # total spots in dataset
        "ranking_method":  "cluster_vs_non_cluster_log2fc",
        "top_genes": [
            {
                "gene":               "SNAP25",   # gene symbol
                "log2_fold_change":   3.81,        # log2(mean_selected / mean_reference)
                "mean_expression":    2.4,         # mean expression in selected spots
                "pct_spots_expressed": 0.87,       # fraction of selected spots expressing it
                "pvalue":             1.2e-5,      # Wilcoxon p-value (add this)
                "adj_pvalue":         3.4e-4,      # BH-corrected p-value (add this)
            },
            ...  # sorted by log2FC descending
        ],
    }
    Return None if h5ad is not loaded or cluster/ROI has no spots.

DO NOT CHANGE the existing output fields — pipeline.py depends on them.
Adding pvalue and adj_pvalue is fine, pipeline.py ignores unknown fields.
"""

from __future__ import annotations
import numpy as np
import scipy
import scipy.sparse
import anndata as ad
from shapely.geometry import Point, Polygon
import niceview.utils.io as vio
from niceview.interface.upload import _spatial_omics_state_path, _spatial_omics_cluster_path


def _rank_high_expression_genes(adata, selected, top_n, ranking_label):
    selected_count = int(selected.sum())
    total_spots = int(adata.n_obs)
    reference_count = total_spots - selected_count
    if selected_count == 0:
        return {
            "selected_spots": 0,
            "reference_spots": reference_count,
            "total_spots": total_spots,
            "top_genes": [],
            "ranking_method": ranking_label,
        }

    selected_matrix = adata[selected].X
    if scipy.sparse.issparse(selected_matrix):
        mean_selected = np.asarray(selected_matrix.mean(axis=0)).ravel()
        pct_selected = np.asarray((selected_matrix > 0).mean(axis=0)).ravel()
    else:
        selected_matrix = np.asarray(selected_matrix)
        mean_selected = selected_matrix.mean(axis=0)
        pct_selected = (selected_matrix > 0).mean(axis=0)

    if reference_count > 0:
        reference_matrix = adata[~selected].X
        if scipy.sparse.issparse(reference_matrix):
            mean_reference = np.asarray(reference_matrix.mean(axis=0)).ravel()
            pct_reference = np.asarray((reference_matrix > 0).mean(axis=0)).ravel()
        else:
            reference_matrix = np.asarray(reference_matrix)
            mean_reference = reference_matrix.mean(axis=0)
            pct_reference = (reference_matrix > 0).mean(axis=0)

        pseudocount = 1e-9
        log2fc = np.log2((mean_selected + pseudocount) / (mean_reference + pseudocount))

        candidate_mask = (mean_selected > 0) & (pct_selected >= 0.05) & (log2fc > 0)
        candidate_indices = np.where(candidate_mask)[0]
        if candidate_indices.size == 0:
            candidate_indices = np.where((mean_selected > 0) & (log2fc > 0))[0]
        if candidate_indices.size == 0:
            candidate_indices = np.where(mean_selected > 0)[0]

        order = np.lexsort((-mean_selected[candidate_indices], -log2fc[candidate_indices]))
        top_indices = candidate_indices[order][:top_n]
        ranking_method = ranking_label
    else:
        mean_reference = np.zeros_like(mean_selected)
        pct_reference = np.zeros_like(pct_selected)
        log2fc = np.zeros_like(mean_selected)
        top_indices = np.argsort(mean_selected)[::-1][:top_n]
        ranking_method = f"{ranking_label}_mean_expression_only_no_reference"

    genes = []
    for idx in top_indices:
        genes.append({
            "gene": str(adata.var_names[idx]),
            "mean_expression": float(mean_selected[idx]),
            "pct_spots_expressed": float(pct_selected[idx]),
            "mean_roi": float(mean_selected[idx]),
            "mean_reference": float(mean_reference[idx]),
            "pct_roi": float(pct_selected[idx]),
            "pct_reference": float(pct_reference[idx]),
            "log2_fold_change": float(log2fc[idx]),
        })

    return {
        "selected_spots": selected_count,
        "reference_spots": reference_count,
        "total_spots": total_spots,
        "top_genes": genes,
        "ranking_method": ranking_method,
    }


def get_roi_high_expression_genes(work_dir, coords, folder_id="", top_n=25):
    """Return genes enriched in selected ROI spots compared with non-ROI spots."""
    state_path = _spatial_omics_state_path(work_dir, folder_id)
    if not coords or not vio.exists(state_path):
        return None

    state = vio.load_json(state_path)
    h5ad_path = state.get("h5ad_path")
    if not h5ad_path or not vio.exists(h5ad_path):
        return None

    adata = ad.read_h5ad(h5ad_path)
    if "spatial" not in adata.obsm:
        return None

    spatial = np.asarray(adata.obsm["spatial"])
    selected = np.zeros(spatial.shape[0], dtype=bool)
    polygons = []
    for coord in coords:
        if coord and len(coord) >= 3:
            polygons.append(Polygon(coord))

    if not polygons:
        return None

    for polygon in polygons:
        selected |= np.array([polygon.covers(Point(x, y)) for x, y in spatial])

    return _rank_high_expression_genes(adata, selected, top_n, "roi_vs_non_roi_log2fc")


def get_cluster_high_expression_genes(work_dir, cluster_id, folder_id="", top_n=25):
    """Return genes enriched in one spatial cluster compared with all other spots."""
    state_path = _spatial_omics_state_path(work_dir, folder_id)
    cluster_path = _spatial_omics_cluster_path(work_dir, folder_id)
    if cluster_id is None or not vio.exists(state_path) or not vio.exists(cluster_path):
        return None

    state = vio.load_json(state_path)
    h5ad_path = state.get("h5ad_path")
    if not h5ad_path or not vio.exists(h5ad_path):
        return None

    cluster_state = vio.load_json(cluster_path)
    clusters = cluster_state.get("clusters", {}) or {}
    cluster_id = str(cluster_id)

    adata = ad.read_h5ad(h5ad_path)
    obs_names = [str(name) for name in adata.obs_names]
    selected = np.array([str(clusters.get(obs_name)) == cluster_id for obs_name in obs_names], dtype=bool)
    result = _rank_high_expression_genes(adata, selected, top_n, "cluster_vs_non_cluster_log2fc")
    result["cluster_id"] = cluster_id
    result["cluster_key"] = cluster_state.get("cluster_key", "spatial_cluster")
    return result
