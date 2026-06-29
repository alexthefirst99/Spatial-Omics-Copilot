import time
import uuid
import os
import anndata as ad
import numpy as np
import pandas as pd
import scipy
import scipy.sparse
from dash import html
from dash import dcc
import niceview.utils.io as vio
import app.status_store as status_store
from niceview.interface.interface import (
    get_data_path_cache_path,
    get_parameter,
    dumpjson_parameter_from_user_input,
    files_generate,
    get_wsi,
)


def S3Upload(id, label="Upload File"):
    return html.Div([
        html.Label(label, className="upload-label"),
        html.Div(className="s3-upload-container", children=[
            dcc.Input(type="file", style={"display": "block", "marginBottom": "5px"}),
            html.Div(className="upload-progress-bar", style={"width": "100%", "backgroundColor": "#e0e0e0", "borderRadius": "5px"}, children=[
                html.Div(className="upload-progress", style={"width": "0%", "height": "5px", "backgroundColor": "#007eff", "borderRadius": "5px", "transition": "width 0.4s"})
            ]),
            html.Div(className="upload-progress-text", style={"fontSize": "10px", "marginTop": "2px", "textAlign": "right"}),
            html.Div(className="upload-status", style={"fontSize": "12px", "marginTop": "5px", "fontStyle": "italic"}),
            dcc.Input(id=id, className="upload-result", type="text", style={"display": "none"})
        ])
    ])


def _spatial_omics_state_path(work_dir, folder_id=""):
    return f'{work_dir}/user{folder_id}/spatial_omics.json'


def _spatial_omics_dir(work_dir, folder_id=""):
    return f'{work_dir}/user{folder_id}/spatial_omics'


def _spatial_omics_cluster_path(work_dir, folder_id=""):
    return f'{_spatial_omics_dir(work_dir, folder_id)}/spatial_clusters.json'


def _cluster_palette(labels):
    colors = [
        "#0071e3", "#ff9500", "#34c759", "#af52de", "#ff3b30",
        "#00c7be", "#5856d6", "#ffcc00", "#5ac8fa", "#ff2d55",
        "#30d158", "#bf5af2", "#ffd60a", "#64d2ff", "#a2845e",
    ]
    return {str(label): colors[i % len(colors)] for i, label in enumerate(sorted(set(map(str, labels))))}


def run_spatial_basic_clustering(h5ad_path, cluster_path):
    """Run a small Scanpy preprocessing workflow and save spatial cluster labels."""
    import scanpy as sc

    adata = ad.read_h5ad(h5ad_path)
    if "spatial" not in adata.obsm:
        raise ValueError('Missing required adata.obsm["spatial"] coordinates.')
    if adata.n_obs < 3 or adata.n_vars < 3:
        raise ValueError("Need at least 3 spots and 3 genes for clustering.")

    adata.var_names_make_unique()

    sc.pp.filter_genes(adata, min_cells=1)
    sc.pp.normalize_total(adata, target_sum=1e4)
    sc.pp.log1p(adata)

    n_top_genes = min(2000, int(adata.n_vars))
    if n_top_genes >= 50:
        try:
            sc.pp.highly_variable_genes(adata, n_top_genes=n_top_genes, flavor="seurat", subset=True)
        except Exception as e:
            print(f"Spatial clustering HVG step skipped: {e}")

    n_comps = min(50, int(adata.n_obs) - 1, int(adata.n_vars) - 1)
    if n_comps < 2:
        raise ValueError("Not enough dimensions for PCA clustering.")
    sc.pp.pca(adata, n_comps=n_comps)
    n_pcs = min(30, n_comps)

    if int(adata.n_obs) > 20000:
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
            print(f"Spatial clustering Leiden failed, using k-means fallback: {e}")
            from sklearn.cluster import KMeans

            method = "pca_kmeans_fallback"
            n_clusters = min(8, max(2, int(round(np.sqrt(float(adata.n_obs) / 2.0)))))
            x_pca = np.asarray(adata.obsm["X_pca"])[:, :n_pcs]
            labels = KMeans(n_clusters=n_clusters, random_state=0, n_init=10).fit_predict(x_pca)
            adata.obs["spatial_cluster"] = pd.Categorical([str(x) for x in labels])

    labels = [str(x) for x in adata.obs["spatial_cluster"].tolist()]
    palette = _cluster_palette(labels)
    clusters = {str(obs_name): label for obs_name, label in zip(adata.obs_names, labels)}
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


def _upload_job_id_from_path(path):
    marker = "data_input_temp/tmp/"
    if marker not in path:
        return None
    try:
        return path.split(marker, 1)[1].split("/", 1)[0]
    except Exception:
        return None


## Note: all the temp data is the original size, the no temp data is resized (if apply)

# upload HE image
def upload_image(filenames_upload_image, folder_id, work_dir, app_dir, job_id=None, finalize_status=True):
    """
    Uploads the HE image and copy it to data path, then create client.

    Parameters:
        filenames_upload_image (list): List of uploaded filenames.
        job_id (str): Optional Job ID for status tracking.

    Returns:
        None
    """

    # get thor parameter and all user input info
    print(f"DEBUG_CHECKPOINT: Entered upload_image with {filenames_upload_image} job_id={job_id}", flush=True)
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)

    # If job_id provided, use it. Otherwise try to extract from path or gen new one.
    upload_path = filenames_upload_image[0]
    upload_uuid = job_id

    if not upload_uuid:
        # Fallback logic
        upload_uuid = None

    # Try to extract UUID from local path pattern: .../data_input_temp/tmp/<uuid>/filename
    if "data_input_temp/tmp/" in upload_path:
        try:
            after = upload_path.split("data_input_temp/tmp/", 1)[1]
            upload_uuid = after.split("/")[0]
        except Exception:
            pass
    # Fallback: legacy S3 pattern .../uploads/<uuid>/filename
    elif "/uploads/" in upload_path:
        parts = upload_path.split("/")
        try:
            idx = parts.index("uploads")
            if idx + 1 < len(parts):
                upload_uuid = parts[idx + 1]
        except Exception:
            pass

    # Fallback to new UUID if extraction fails (shouldn't happen with our JS)
    if not upload_uuid:
        upload_uuid = str(uuid.uuid1())

    # Use extracted UUID as sample_id? Or keep using uuid1?
    # Original logic used uuid1(). Let's stick to extraction for consistency with frontend progress bar.
    sample_id = upload_uuid

    status_store.update_status(upload_uuid, 0, "Wait for file check...")

    # get basename of upload image
    basename = os.path.splitext(os.path.basename(filenames_upload_image[0]))[0]

    print(f"DEBUG: Callback triggered for {upload_uuid}. Path: {filenames_upload_image[0]}")

    # wait and chech if file exist in folder
    status_store.update_status(upload_uuid, 5, "Checking file availability...")
    print(f"DEBUG_CHECKPOINT: Checking vio.exists for {filenames_upload_image[0]}", flush=True)
    while not vio.exists(filenames_upload_image[0]):
        print("DEBUG: Waiting for file...")
        time.sleep(5)

    print("DEBUG: File found. Starting processing.")
    status_store.update_status(upload_uuid, 20, "Processing Data Dimensions...")

    # get the height and width of image to calculate max dimension
    print(f"DEBUG_CHECKPOINT: Calling thor.process_data for {filenames_upload_image[0]}", flush=True)
    try:
        height, width = thor.process_data(sample_id, img_path=filenames_upload_image[0])
        print(f"DEBUG: process_data returned: {height}, {width}")
    except Exception as e:
        print(f"DEBUG: process_data FAILED: {e}")
        raise e

    # update it in user argument file
    args["heightWidth"] = [height, width]

    args['sampleId'] = sample_id
    args['fileName'] = basename
    args.pop("tutorialImagePath", None)
    dumpjson_parameter_from_user_input(folder_id, work_dir, args=args)

    # get the name rules for further calculation, then copy it to data path
    files = files_generate(sample_id)

    src_path = filenames_upload_image[0]
    dst_path = vio.join_path(data_path, files["img"])
    print(f"DEBUG: Copying from {src_path} to {dst_path}")
    vio.copy(src_path, dst_path)

    # calculate wsi of image BEFORE deleting the local file to avoid S3 re-download
    status_store.update_status(upload_uuid, 50, "Generating WSI Tiling...")
    print("DEBUG: Calling get_wsi...")
    get_wsi(folder_id, work_dir, local_img_path=src_path)

    status_store.update_status(upload_uuid, 80, "Cleaning temporary upload...")
    try:
        # Cleanup S3 upload - delay deletion until end so all funcs can use local path
        if src_path and os.path.exists(src_path):
            print(f"DEBUG: Removing original upload {src_path}")
            vio.remove(src_path)

        # CLEANUP: Delete the local uploaded file and its folder
        try:
            if upload_path and os.path.exists(upload_path):
                # Only delete if it is NOT an S3 path (which starts with s3://)
                # dash_uploader provides local paths like /path/to/upload_dir/...
                if not upload_path.startswith("s3://"):
                    os.remove(upload_path)
                    print(f"DEBUG: Deleted local data_input_temp file: {upload_path}")

                    # Try to remove the parent directory if empty (Dash Uploader creates one folder per upload)
                    parent_dir = os.path.dirname(upload_path)
                    if not os.listdir(parent_dir):
                        os.rmdir(parent_dir)
                        print(f"DEBUG: Removed empty parent folder: {parent_dir}")
        except Exception as e:
            print(f"WARNING: Failed to cleanup local file {upload_path}: {e}")

        # Some callers do additional post-processing before the upload should
        # be considered complete.
        if finalize_status:
            status_store.update_status(upload_uuid, 100, "Complete")
    except Exception as e:
        print(f"WARNING: Cleanup error: {e}")
    return None


def upload_spatial_h5ad(filenames_upload_h5ad, folder_id, work_dir):
    """Register a spatial AnnData file for later ROI-level AI analysis."""
    import dash
    if not filenames_upload_h5ad:
        return dash.no_update

    source_path = filenames_upload_h5ad[0]
    job_id = _upload_job_id_from_path(source_path)
    if job_id:
        status_store.update_status(job_id, 5, "Checking h5ad file...")

    try:
        while not vio.exists(source_path):
            time.sleep(1)

        spatial_dir = _spatial_omics_dir(work_dir, folder_id)
        vio.ensure_dir(spatial_dir)
        stored_path = vio.join_path(spatial_dir, "spatial_expression.h5ad")

        if job_id:
            status_store.update_status(job_id, 20, "Saving h5ad file...")
        vio.copy(source_path, stored_path)

        if job_id:
            status_store.update_status(job_id, 45, "Validating spatial coordinates...")
        adata = ad.read_h5ad(stored_path, backed="r")
        has_spatial = "spatial" in adata.obsm
        if not has_spatial:
            raise ValueError('Missing required adata.obsm["spatial"] coordinates.')

        n_obs = int(adata.n_obs)
        n_vars = int(adata.n_vars)
        preview_genes = list(map(str, adata.var_names[:8]))
        if getattr(adata, "file", None) is not None:
            adata.file.close()

        state = {
            "h5ad_path": stored_path,
            "n_spots": n_obs,
            "n_genes": n_vars,
            "spatial_key": "spatial",
            "preview_genes": preview_genes,
        }

        cluster_summary = None
        cluster_error = None
        try:
            if job_id:
                status_store.update_status(job_id, 70, "Running basic clustering...")
            cluster_path = _spatial_omics_cluster_path(work_dir, folder_id)
            cluster_summary = run_spatial_basic_clustering(stored_path, cluster_path)
            state.update({
                "cluster_path": cluster_path,
                "cluster_key": cluster_summary.get("cluster_key", "spatial_cluster"),
                "cluster_method": cluster_summary.get("method"),
                "n_clusters": cluster_summary.get("n_clusters"),
            })
        except Exception as e:
            cluster_error = str(e)
            state["cluster_error"] = cluster_error
            print(f"Spatial clustering skipped: {cluster_error}")

        if job_id:
            status_store.update_status(job_id, 90, "Saving h5ad metadata...")
        vio.dump_json(state, _spatial_omics_state_path(work_dir, folder_id), indent=2)

        if job_id:
            status_store.update_status(job_id, 100, "h5ad ready for ROI analysis")

        summary_children = [
            html.Div("h5ad ready", className="omics-upload-title"),
            html.Div(className="omics-upload-stats", children=[
                html.Span(f"{n_obs:,} spots"),
                html.Span(f"{n_vars:,} genes"),
            ]),
        ]
        if cluster_summary:
            summary_children.append(
                html.Div(
                    f"{cluster_summary.get('n_clusters', 0)} spatial clusters ready for viewer overlay",
                    className="omics-upload-genes"
                )
            )
        elif cluster_error:
            summary_children.append(
                html.Div(f"Clustering skipped: {cluster_error}", className="omics-upload-genes")
            )
        summary_children.append(
            html.Div("Example: " + ", ".join(preview_genes), className="omics-upload-genes")
        )

        return html.Div(className="omics-upload-summary", children=summary_children)
    except Exception as e:
        if 'stored_path' in locals() and vio.exists(stored_path):
            vio.remove(stored_path)
        if job_id:
            status_store.update_status(job_id, 100, "h5ad upload failed")
        return html.Div(className="omics-upload-summary error", children=[
            html.Div("h5ad upload failed", className="omics-upload-title"),
            html.Div(str(e), className="omics-upload-genes"),
        ])
    finally:
        if source_path and 'stored_path' in locals() and source_path != stored_path and vio.exists(source_path):
            vio.remove(source_path)
