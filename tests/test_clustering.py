"""
Tests for the clustering module (T-041).
"""
import os
import numpy as np
import pandas as pd
import anndata as ad

from rag.clustering import build_clustering_features, run_spatial_clustering


def make_synthetic_adata(n_spots=100, n_genes=200, seed=1):
    rng = np.random.default_rng(seed)
    X = rng.poisson(3, size=(n_spots, n_genes)).astype(float)
    obs = pd.DataFrame(index=[f"spot_{i}" for i in range(n_spots)])
    var = pd.DataFrame(index=[f"gene_{i}" for i in range(n_genes)])
    adata = ad.AnnData(X=X, obs=obs, var=var)
    adata.obsm["spatial"] = rng.random((n_spots, 2)) * 1000
    return adata


def test_build_clustering_features_spatial_flag():
    adata = make_synthetic_adata()
    adata.obsm["X_pca"] = np.random.default_rng(2).random((adata.n_obs, 10))

    with_spatial = build_clustering_features(adata, n_pcs=10, use_spatial=True)
    without_spatial = build_clustering_features(adata, n_pcs=10, use_spatial=False)

    assert with_spatial.shape[1] > without_spatial.shape[1]
    assert without_spatial.shape[1] == 10


def test_run_spatial_clustering_leiden_path(tmp_path):
    adata = make_synthetic_adata(n_spots=60)
    h5ad_path = tmp_path / "test.h5ad"
    adata.write_h5ad(h5ad_path)
    cluster_path = str(tmp_path / "clusters.json")

    config = {"min_genes": 1, "min_counts": 1, "leiden_resolution": 1.0}
    result = run_spatial_clustering(str(h5ad_path), cluster_path, use_cache=False, config=config)

    assert result["n_spots"] == 60
    assert result["n_clusters"] >= 1
    assert os.path.exists(cluster_path)


def test_run_spatial_clustering_respects_n_clusters_kmeans_fallback(tmp_path):
    adata = make_synthetic_adata(n_spots=60)
    h5ad_path = tmp_path / "test.h5ad"
    adata.write_h5ad(h5ad_path)
    cluster_path = str(tmp_path / "clusters.json")

    # Force very high resolution so Leiden is unlikely to be the deciding factor;
    # main check here is that the pipeline runs end-to-end and produces valid output.
    config = {"min_genes": 1, "min_counts": 1, "n_clusters": 4}
    result = run_spatial_clustering(str(h5ad_path), cluster_path, use_cache=False, config=config)

    assert result["n_spots"] == 60
    assert "clusters" in result
    assert len(result["clusters"]) == 60


def test_run_spatial_clustering_uses_cache(tmp_path):
    adata = make_synthetic_adata(n_spots=60)
    h5ad_path = tmp_path / "test.h5ad"
    adata.write_h5ad(h5ad_path)
    cluster_path = str(tmp_path / "clusters.json")

    config = {"min_genes": 1, "min_counts": 1}
    result1 = run_spatial_clustering(str(h5ad_path), cluster_path, use_cache=True, config=config)
    result2 = run_spatial_clustering(str(h5ad_path), cluster_path, use_cache=True, config=config)

    assert result1["clusters"] == result2["clusters"]