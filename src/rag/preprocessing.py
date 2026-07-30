"""
Preprocessing Module
Teammate responsibility: quality control, normalization, feature selection,
and dimensionality reduction on spatial transcriptomics h5ad data.

Steps:
  1. Filter low-quality spots (QC) and low-quality genes
  2. Store raw counts before normalization
  3. Normalize total counts per spot
  4. Log1p transformation
  5. Highly variable gene selection (HVG)
  6. PCA

Returns a preprocessed AnnData object ready for clustering.
"""

from __future__ import annotations
import os
import json
import hashlib
import anndata as ad
import scanpy as sc
import numpy as np


def run_spot_qc(adata: ad.AnnData, min_genes: int = 200, min_counts: int = 500) -> ad.AnnData:
    """Remove low-quality spots (T-033).

    Drops spots with too few detected genes or too low total counts.
    """
    sc.pp.filter_cells(adata, min_genes=min_genes)
    sc.pp.filter_cells(adata, min_counts=min_counts)
    return adata


def store_raw_counts(adata: ad.AnnData) -> ad.AnnData:
    """Save the original count matrix before normalization changes it (T-034)."""
    adata.layers["counts"] = adata.X.copy()
    return adata


def preprocess_adata(h5ad_path: str, config: dict | None = None) -> tuple:
    """Load and preprocess a spatial h5ad file.

    Args:
        h5ad_path: path to the .h5ad file
        config: optional dict with keys min_genes, min_counts, n_hvg, n_pcs

    Returns:
        adata   — preprocessed AnnData with X_pca in obsm
        n_pcs   — number of PCA components computed (used by clustering)

    Raises:
        ValueError if spatial coordinates are missing or dataset is too small.
    """
    config = config or {}
    adata = ad.read_h5ad(h5ad_path)

    if "spatial" not in adata.obsm:
        raise ValueError('Missing required adata.obsm["spatial"] coordinates.')
    if adata.n_obs < 3 or adata.n_vars < 3:
        raise ValueError("Need at least 3 spots and 3 genes for clustering.")

    adata.var_names_make_unique()

    existing_pca = adata.obsm.get("X_pca")
    if existing_pca is not None and getattr(existing_pca, "shape", (0, 0))[0] == adata.n_obs:
        n_comps = int(existing_pca.shape[1])
        if n_comps >= 2:
            return adata, min(30, n_comps)

    # T-033: Spot-level QC filtering
    adata = run_spot_qc(
        adata,
        min_genes=config.get("min_genes", 200),
        min_counts=config.get("min_counts", 500),
    )

    # QC filtering on genes (existing behavior)
    sc.pp.filter_genes(adata, min_cells=1)

    # T-034: Store raw counts before normalization
    adata = store_raw_counts(adata)

    # Normalization
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)

    # Highly variable gene selection
    n_top_genes = min(config.get("n_hvg", 2000), int(adata.n_vars))
    if n_top_genes >= 50:
        try:
            sc.pp.highly_variable_genes(
                adata, n_top_genes=n_top_genes, flavor="seurat", subset=True
            )
        except Exception as e:
            print(f"[preprocessing] HVG step skipped: {e}")

    # PCA
    n_comps = min(config.get("n_pcs", 50), int(adata.n_obs) - 1, int(adata.n_vars) - 1)
    if n_comps < 2:
        raise ValueError("Not enough dimensions for PCA.")
    sc.pp.pca(adata, n_comps=n_comps)
    n_pcs = min(30, n_comps)

    return adata, n_pcs


def _qc_summary(adata: ad.AnnData) -> dict:
    return {"n_spots": int(adata.n_obs), "n_genes": int(adata.n_vars)}


def _cache_key(h5ad_path: str, config: dict) -> str:
    payload = json.dumps({"path": str(h5ad_path), "config": config}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def preprocess_h5ad(h5ad_path: str, config: dict | None = None) -> dict:
    """Cached top-level entry point (T-035).

    Runs preprocess_adata, caches the result to disk, and returns a
    dict payload with adata_path and qc_summary. Reuses the cache when
    the same input file and config were already processed.
    """
    config = config or {}
    cache_dir = config.get("preprocess_cache_dir", "tmp_data/preprocess_cache")
    os.makedirs(cache_dir, exist_ok=True)

    key = _cache_key(h5ad_path, config)
    cache_path = os.path.join(cache_dir, f"{key}.h5ad")

    if os.path.exists(cache_path):
        adata = ad.read_h5ad(cache_path)
        return {
            "adata_path": cache_path,
            "qc_summary": _qc_summary(adata),
            "from_cache": True,
        }

    adata, _n_pcs = preprocess_adata(h5ad_path, config)
    adata.write_h5ad(cache_path)

    return {
        "adata_path": cache_path,
        "qc_summary": _qc_summary(adata),
        "from_cache": False,
    }