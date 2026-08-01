"""
Tests for the preprocessing module (T-036).
"""
import os
import numpy as np
import pandas as pd
import anndata as ad
import pytest

from rag.preprocessing import (
    run_spot_qc,
    store_raw_counts,
    preprocess_adata,
    preprocess_h5ad,
)


def make_synthetic_adata(n_spots=100, n_genes=200, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.poisson(3, size=(n_spots, n_genes)).astype(float)
    obs = pd.DataFrame(index=[f"spot_{i}" for i in range(n_spots)])
    var = pd.DataFrame(index=[f"gene_{i}" for i in range(n_genes)])
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = rng.random((n_spots, 2)) * 1000
    return adata


def test_run_spot_qc_removes_low_quality_spots():
    adata = make_synthetic_adata()
    # Zero out a few spots so they fail QC
    adata.X[0, :] = 0
    adata.X[1, :] = 0
    original_n_obs = adata.n_obs  # capture BEFORE filtering (filter_cells mutates in place)
    filtered = run_spot_qc(adata, min_genes=1, min_counts=1)
    assert filtered.n_obs < original_n_obs


def test_store_raw_counts_matches_original():
    adata = make_synthetic_adata()
    original = adata.X.copy()
    adata = store_raw_counts(adata)
    assert "counts" in adata.layers
    assert np.allclose(adata.layers["counts"], original)


def test_preprocess_adata_returns_pca_and_n_pcs():
    adata = make_synthetic_adata()
    tmp_path = "tmp_data/test_synthetic.h5ad"
    os.makedirs("tmp_data", exist_ok=True)
    adata.write_h5ad(tmp_path)

    processed_adata, n_pcs = preprocess_adata(tmp_path, config={"min_genes": 1, "min_counts": 1})
    assert "X_pca" in processed_adata.obsm
    assert n_pcs >= 2

    os.remove(tmp_path)


def test_preprocess_adata_raises_without_spatial():
    adata = make_synthetic_adata()
    del adata.obsm["spatial"]
    tmp_path = "tmp_data/test_no_spatial.h5ad"
    os.makedirs("tmp_data", exist_ok=True)
    adata.write_h5ad(tmp_path)

    with pytest.raises(ValueError):
        preprocess_adata(tmp_path)

    os.remove(tmp_path)


def test_preprocess_h5ad_caches(tmp_path):
    adata = make_synthetic_adata()
    h5ad_path = tmp_path / "test.h5ad"
    adata.write_h5ad(h5ad_path)

    config = {
        "min_genes": 1,
        "min_counts": 1,
        "preprocess_cache_dir": str(tmp_path / "cache"),
    }

    result1 = preprocess_h5ad(str(h5ad_path), config)
    assert result1["qc_summary"]["n_spots"] > 0
    assert result1["from_cache"] is False

    result2 = preprocess_h5ad(str(h5ad_path), config)
    assert result2["adata_path"] == result1["adata_path"]
    assert result2["from_cache"] is True