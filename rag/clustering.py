"""
Clustering Module
Teammate responsibility: spatial cluster assignment using graph-based
(Leiden) or centroid-based (KMeans) methods on PCA-reduced expression data.

Strategy:
  - >20 000 spots → MiniBatchKMeans on PCA (speed)
  - ≤20 000 spots → Scanpy Leiden graph clustering
  - Leiden failure  → KMeans fallback

Saves cluster labels + palette to cluster_path (JSON).
"""

from __future__ import annotations
import numpy as np
import pandas as pd
import scanpy as sc
import niceview.utils.io as vio

from rag.preprocessing import preprocess_adata


def _cluster_palette(labels: list[str]) -> dict[str, str]:
    # Colors chosen so that no two within the first 20 indices look visually similar.
    colors = [
        "#0071e3", "#ff3b30", "#34c759", "#ff9500", "#af52de",
        "#00c7be", "#ff2d55", "#5856d6", "#ffcc00", "#1db954",
        "#5ac8fa", "#e040fb", "#ffd60a", "#ff6b35", "#64d2ff",
        "#a2845e", "#00bcd4", "#f06292", "#aed581", "#9575cd",
        "#4db6ac", "#ffb74d", "#4fc3f7", "#f48fb1", "#80cbc4",
        "#ce93d8", "#ef9a9a", "#80deea", "#c5e1a5", "#ffe082",
        "#b0bec5", "#bcaaa4", "#ff8a65", "#bf5af2", "#30d158",
    ]

    def _sort_key(label):
        try:
            return (0, int(label))
        except ValueError:
            return (1, label)

    sorted_labels = sorted(set(map(str, labels)), key=_sort_key)
    return {label: colors[i % len(colors)] for i, label in enumerate(sorted_labels)}


def run_spatial_clustering(h5ad_path: str, cluster_path: str) -> dict:
    """Preprocess and cluster a spatial h5ad file.

    Saves results to cluster_path and returns the cluster payload dict.
    """
    adata, n_pcs = preprocess_adata(h5ad_path)

    if int(adata.n_obs) > 20_000:
        from sklearn.cluster import MiniBatchKMeans

        method = "pca_minibatch_kmeans_over_20k"
        n_clusters = min(12, max(4, int(round(np.sqrt(float(adata.n_obs) / 3000.0)))))
        x_pca = np.asarray(adata.obsm["X_pca"])[:, :n_pcs]
        labels = MiniBatchKMeans(
            n_clusters=n_clusters,
            random_state=0,
            batch_size=min(8192, int(adata.n_obs)),
            n_init=5,
        ).fit_predict(x_pca)
        adata.obs["spatial_cluster"] = pd.Categorical([str(x) for x in labels])
    else:
        sc.pp.neighbors(adata, n_neighbors=min(15, max(2, int(adata.n_obs) - 1)), n_pcs=n_pcs)
        method = "scanpy_leiden"
        try:
            sc.tl.leiden(adata, key_added="spatial_cluster", resolution=0.8)
        except Exception as e:
            print(f"[clustering] Leiden failed, using KMeans fallback: {e}")
            from sklearn.cluster import KMeans

            method = "pca_kmeans_fallback"
            n_clusters = min(8, max(2, int(round(np.sqrt(float(adata.n_obs) / 2.0)))))
            x_pca = np.asarray(adata.obsm["X_pca"])[:, :n_pcs]
            labels = KMeans(n_clusters=n_clusters, random_state=0, n_init=10).fit_predict(x_pca)
            adata.obs["spatial_cluster"] = pd.Categorical([str(x) for x in labels])

    labels = [str(x) for x in adata.obs["spatial_cluster"].tolist()]
    palette = _cluster_palette(labels)
    clusters = {str(obs): label for obs, label in zip(adata.obs_names, labels)}

    payload = {
        "cluster_key": "spatial_cluster",
        "method": method,
        "n_spots": int(adata.n_obs),
        "n_clusters": len(set(labels)),
        "clusters": clusters,
        "palette": palette,
    }
    vio.dump_json(payload, cluster_path, indent=2)
    return payload
