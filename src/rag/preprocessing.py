"""
Preprocessing Module
Teammate responsibility: quality control, normalization, feature selection,
and dimensionality reduction on spatial transcriptomics h5ad data.

Steps:
  1. Filter low-quality genes
  2. Normalize total counts per spot
  3. Log1p transformation
  4. Highly variable gene selection (HVG)
  5. PCA

Returns a preprocessed AnnData object ready for clustering.
"""

from __future__ import annotations
import anndata as ad
import scanpy as sc
import numpy as np


def preprocess_adata(h5ad_path: str) -> tuple:
    """Load and preprocess a spatial h5ad file.

    Returns:
        adata   — preprocessed AnnData with X_pca in obsm
        n_pcs   — number of PCA components computed (used by clustering)

    Raises:
        ValueError if spatial coordinates are missing or dataset is too small.
    """
    adata = ad.read_h5ad(h5ad_path)

    if "spatial" not in adata.obsm:
        raise ValueError('Missing required adata.obsm["spatial"] coordinates.')
    if adata.n_obs < 3 or adata.n_vars < 3:
        raise ValueError("Need at least 3 spots and 3 genes for clustering.")

    adata.var_names_make_unique()

    # QC filtering
    sc.pp.filter_genes(adata, min_cells=1)

    # Normalization
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)

    # Highly variable gene selection
    n_top_genes = min(2000, int(adata.n_vars))
    if n_top_genes >= 50:
        try:
            sc.pp.highly_variable_genes(
                adata, n_top_genes=n_top_genes, flavor="seurat", subset=True
            )
        except Exception as e:
            print(f"[preprocessing] HVG step skipped: {e}")

    # PCA
    n_comps = min(50, int(adata.n_obs) - 1, int(adata.n_vars) - 1)
    if n_comps < 2:
        raise ValueError("Not enough dimensions for PCA.")
    sc.pp.pca(adata, n_comps=n_comps)
    n_pcs = min(30, n_comps)

    return adata, n_pcs
