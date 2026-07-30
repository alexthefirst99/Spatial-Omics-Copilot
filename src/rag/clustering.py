"""
Clustering Module
Teammate responsibility: spatial cluster assignment using graph-based
(Leiden) or centroid-based (KMeans) methods on PCA-reduced expression data.

Strategy:
  - >20 000 spots → MiniBatchKMeans on PCA (speed)
  - ≤20 000 spots → Scanpy Leiden graph clustering
  - Leiden failure  → KMeans fallback

Config options (all optional):
  - leiden_resolution: float, controls Leiden cluster granularity (T-037)
  - n_clusters: int, user-specified KMeans cluster count (T-039)
  - use_spatial: bool, combine spatial coordinates with PCA features (T-038)

Saves cluster labels + palette to cluster_path (JSON).
"""

from __future__ import annotations
import os
import numpy as np
import pandas as pd
import anndata as ad
import scanpy as sc
import niceview.utils.io as vio

from rag.preprocessing import preprocess_adata


def build_clustering_features(adata: ad.AnnData, n_pcs: int, use_spatial: bool = True) -> np.ndarray:
    """Combine PCA features with normalized spatial coordinates (T-038).

    Returns a feature matrix used for KMeans-based clustering. When
    use_spatial is False, or spatial coordinates are unavailable,
    returns PCA features only.
    """
    pca_features = np.asarray(adata.obsm["X_pca"])[:, :n_pcs]

    if not use_spatial or "spatial" not in adata.obsm:
        return pca_features

    from sklearn.preprocessing import StandardScaler

    spatial = np.asarray(adata.obsm["spatial"])
    spatial_scaled = StandardScaler().fit_transform(spatial)
    return np.hstack([pca_features, spatial_scaled])


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


def _cluster_cache_is_current(h5ad_path: str, cluster_path: str) -> bool:
    if not vio.exists(cluster_path):
        return False
    try:
        payload = vio.load_json(cluster_path)
        source = payload.get("source", {}) if isinstance(payload, dict) else {}
        return (
            source.get("h5ad_path") == h5ad_path
            and float(source.get("h5ad_mtime", -1)) >= float(os.path.getmtime(h5ad_path))
            and int(source.get("h5ad_size", -1)) == int(os.path.getsize(h5ad_path))
        )
    except Exception:
        return False


def _payload_from_labels(h5ad_path: str, cluster_path: str, labels, obs_names, method: str) -> dict:
    labels = [str(x) for x in labels]
    palette = _cluster_palette(labels)
    clusters = {str(obs): label for obs, label in zip(obs_names, labels)}

    payload = {
        "cluster_key": "spatial_cluster",
        "method": method,
        "n_spots": len(labels),
        "n_clusters": len(set(labels)),
        "clusters": clusters,
        "palette": palette,
        "source": {
            "h5ad_path": h5ad_path,
            "h5ad_mtime": os.path.getmtime(h5ad_path),
            "h5ad_size": os.path.getsize(h5ad_path),
        },
    }
    vio.dump_json(payload, cluster_path, indent=2)
    return payload


def _reuse_existing_cluster_labels(h5ad_path: str, cluster_path: str) -> dict | None:
    adata = ad.read_h5ad(h5ad_path, backed="r")
    try:
        if "spatial" not in adata.obsm:
            return None
        for key in ("spatial_cluster", "leiden", "louvain", "cluster", "clusters"):
            if key in adata.obs:
                return _payload_from_labels(
                    h5ad_path,
                    cluster_path,
                    adata.obs[key].tolist(),
                    adata.obs_names,
                    f"existing_obs_{key}",
                )
    finally:
        if getattr(adata, "file", None) is not None:
            adata.file.close()
    return None


def run_spatial_clustering(
    h5ad_path: str,
    cluster_path: str,
    *,
    use_cache: bool = True,
    config: dict | None = None,
) -> dict:
    """Preprocess and cluster a spatial h5ad file.

    Saves results to cluster_path and returns the cluster payload dict.
    """
    config = config or {}

    if use_cache and _cluster_cache_is_current(h5ad_path, cluster_path):
        return vio.load_json(cluster_path)

    existing_payload = _reuse_existing_cluster_labels(h5ad_path, cluster_path)
    if existing_payload is not None:
        return existing_payload

    adata, n_pcs = preprocess_adata(h5ad_path, config)
    use_spatial = config.get("use_spatial", True)

    if int(adata.n_obs) > 20_000:
        from sklearn.cluster import MiniBatchKMeans

        method = "pca_minibatch_kmeans_over_20k"
        n_clusters = config.get("n_clusters") or min(
            12, max(4, int(round(np.sqrt(float(adata.n_obs) / 3000.0))))
        )
        features = build_clustering_features(adata, n_pcs, use_spatial=use_spatial)
        labels = MiniBatchKMeans(
            n_clusters=n_clusters,
            random_state=0,
            batch_size=min(8192, int(adata.n_obs)),
            n_init=5,
        ).fit_predict(features)
        adata.obs["spatial_cluster"] = pd.Categorical([str(x) for x in labels])
    else:
        sc.pp.neighbors(adata, n_neighbors=min(15, max(2, int(adata.n_obs) - 1)), n_pcs=n_pcs)
        method = "scanpy_leiden"
        leiden_resolution = config.get("leiden_resolution", 0.8)
        try:
            sc.tl.leiden(adata, key_added="spatial_cluster", resolution=leiden_resolution)
        except Exception as e:
            print(f"[clustering] Leiden failed, using KMeans fallback: {e}")
            from sklearn.cluster import KMeans

            method = "pca_kmeans_fallback"
            n_clusters = config.get("n_clusters") or min(
                8, max(2, int(round(np.sqrt(float(adata.n_obs) / 2.0))))
            )
            features = build_clustering_features(adata, n_pcs, use_spatial=use_spatial)
            labels = KMeans(n_clusters=n_clusters, random_state=0, n_init=10).fit_predict(features)
            adata.obs["spatial_cluster"] = pd.Categorical([str(x) for x in labels])

    return _payload_from_labels(
        h5ad_path,
        cluster_path,
        adata.obs["spatial_cluster"].tolist(),
        adata.obs_names,
        method,
    )