import numpy as np
import scipy
import scipy.sparse
import anndata as ad
from shapely.geometry import Point, Polygon
import niceview.utils.io as vio
from niceview.interface.upload import _spatial_omics_state_path


def get_roi_high_expression_genes(work_dir, coords, folder_id="", top_n=15):
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

    selected_count = int(selected.sum())
    total_spots = int(adata.n_obs)
    reference_count = total_spots - selected_count
    if selected_count == 0:
        return {
            "selected_spots": 0,
            "reference_spots": reference_count,
            "total_spots": total_spots,
            "top_genes": [],
            "ranking_method": "roi_vs_non_roi_log2fc",
        }

    roi_matrix = adata[selected].X
    if scipy.sparse.issparse(roi_matrix):
        mean_roi = np.asarray(roi_matrix.mean(axis=0)).ravel()
        pct_roi = np.asarray((roi_matrix > 0).mean(axis=0)).ravel()
    else:
        roi_matrix = np.asarray(roi_matrix)
        mean_roi = roi_matrix.mean(axis=0)
        pct_roi = (roi_matrix > 0).mean(axis=0)

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
        log2fc = np.log2((mean_roi + pseudocount) / (mean_reference + pseudocount))

        candidate_mask = (mean_roi > 0) & (pct_roi >= 0.05) & (log2fc > 0)
        candidate_indices = np.where(candidate_mask)[0]
        if candidate_indices.size == 0:
            candidate_indices = np.where((mean_roi > 0) & (log2fc > 0))[0]
        if candidate_indices.size == 0:
            candidate_indices = np.where(mean_roi > 0)[0]

        order = np.lexsort((-mean_roi[candidate_indices], -log2fc[candidate_indices]))
        top_indices = candidate_indices[order][:top_n]
        ranking_method = "roi_vs_non_roi_log2fc"
    else:
        mean_reference = np.zeros_like(mean_roi)
        pct_reference = np.zeros_like(pct_roi)
        log2fc = np.zeros_like(mean_roi)
        top_indices = np.argsort(mean_roi)[::-1][:top_n]
        ranking_method = "roi_mean_expression_only_no_reference"

    genes = []
    for idx in top_indices:
        genes.append({
            "gene": str(adata.var_names[idx]),
            "mean_expression": float(mean_roi[idx]),
            "pct_spots_expressed": float(pct_roi[idx]),
            "mean_roi": float(mean_roi[idx]),
            "mean_reference": float(mean_reference[idx]),
            "pct_roi": float(pct_roi[idx]),
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


def build_roi_gene_context(work_dir, coords, folder_id="", top_n=15):
    result = get_roi_high_expression_genes(work_dir, coords, folder_id=folder_id, top_n=top_n)
    if not result:
        return ""

    if result["selected_spots"] == 0:
        return (
            "\n\nSpatial omics context: The selected ROI did not overlap any spots "
            "from the uploaded h5ad file."
        )

    lines = [
        "\n\nSpatial omics context from uploaded h5ad:",
        f"- ROI spots: {result['selected_spots']} of {result['total_spots']}",
        f"- Reference non-ROI spots: {result.get('reference_spots', 0)}",
        "- ROI-enriched marker candidates ranked by log2 fold change:",
    ]
    for gene in result["top_genes"]:
        lines.append(
            f"  - {gene['gene']}: log2FC={gene['log2_fold_change']:.3g}, "
            f"ROI_mean={gene['mean_roi']:.4g}, nonROI_mean={gene['mean_reference']:.4g}, "
            f"ROI_pct={gene['pct_roi']:.0%}, nonROI_pct={gene['pct_reference']:.0%}"
        )
    return "\n".join(lines)
