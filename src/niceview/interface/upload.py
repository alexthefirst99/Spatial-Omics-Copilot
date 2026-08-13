import time
import uuid
import os
import threading
import anndata as ad
from dash import html
from dash import dcc
import niceview.utils.io as vio
from rag.clustering import run_spatial_clustering
import app.status_store as status_store
from niceview.interface.interface import (
    get_data_path_cache_path,
    get_parameter,
    dumpjson_parameter_from_user_input,
    files_generate,
    get_wsi,
)


FILE_WAIT_TIMEOUT_SECONDS = 30


def _wait_for_file(path, timeout_seconds=FILE_WAIT_TIMEOUT_SECONDS, poll_seconds=0.25):
    """Wait briefly for an upload to become visible, then fail instead of polling forever."""
    deadline = time.monotonic() + max(0, float(timeout_seconds))
    while not vio.exists(path):
        if time.monotonic() >= deadline:
            raise FileNotFoundError(
                f"Uploaded file was not found after {timeout_seconds:g} seconds: {path}. "
                "Please select the file and upload it again."
            )
        time.sleep(poll_seconds)


def _spatial_omics_state_path(work_dir, folder_id=""):
    return f'{work_dir}/user{folder_id}/spatial_omics.json'


def _spatial_omics_dir(work_dir, folder_id=""):
    return f'{work_dir}/user{folder_id}/spatial_omics'


def _spatial_omics_cluster_path(work_dir, folder_id=""):
    return f'{_spatial_omics_dir(work_dir, folder_id)}/spatial_clusters.json'


def _upload_job_id_from_path(path):
    marker = "data_input_temp/tmp/"
    if marker not in path:
        return None
    try:
        return path.split(marker, 1)[1].split("/", 1)[0]
    except Exception:
        return None


def _run_spatial_clustering_background(stored_path, cluster_path, state_path, job_id=None):
    try:
        if job_id:
            status_store.update_status(job_id, 85, "Running basic clustering in background...")

        cluster_summary = run_spatial_clustering(stored_path, cluster_path)

        state = vio.load_json(state_path) if vio.exists(state_path) else {}
        state.update({
            "cluster_path": cluster_path,
            "cluster_key": cluster_summary.get("cluster_key", "spatial_cluster"),
            "cluster_method": cluster_summary.get("method"),
            "n_clusters": cluster_summary.get("n_clusters"),
            "cluster_status": "ready",
        })
        state.pop("cluster_error", None)
        vio.dump_json(state, state_path, indent=2)

        if job_id:
            status_store.update_status(job_id, 100, "h5ad ready; clustering complete")
    except Exception as e:
        cluster_error = str(e)
        print(f"Spatial clustering skipped: {cluster_error}")
        try:
            state = vio.load_json(state_path) if vio.exists(state_path) else {}
            state.update({
                "cluster_status": "failed",
                "cluster_error": cluster_error,
            })
            vio.dump_json(state, state_path, indent=2)
        except Exception as state_error:
            print(f"Failed to save clustering error state: {state_error}")
        if job_id:
            status_store.update_status(job_id, 100, "h5ad ready; clustering skipped")


def _start_spatial_clustering_background(stored_path, cluster_path, state_path, job_id=None):
    thread = threading.Thread(
        target=_run_spatial_clustering_background,
        args=(stored_path, cluster_path, state_path, job_id),
        daemon=True,
        name="spatial-clustering",
    )
    thread.start()
    return thread


def upload_image(filenames_upload_image, folder_id, work_dir, app_dir, job_id=None, finalize_status=True):
    """
    Uploads the HE image and copy it to data path, then create client.

    Parameters:
        filenames_upload_image (list): List of uploaded filenames.
        job_id (str): Optional Job ID for status tracking.

    Returns:
        None
    """

    print(f"DEBUG_CHECKPOINT: Entered upload_image with {filenames_upload_image} job_id={job_id}", flush=True)
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)

    upload_path = filenames_upload_image[0]
    upload_uuid = job_id

    if not upload_uuid:
        upload_uuid = None

    # Recover the upload ID from current and legacy path layouts.
    if "data_input_temp/tmp/" in upload_path:
        try:
            after = upload_path.split("data_input_temp/tmp/", 1)[1]
            upload_uuid = after.split("/")[0]
        except Exception:
            pass
    elif "/uploads/" in upload_path:
        parts = upload_path.split("/")
        try:
            idx = parts.index("uploads")
            if idx + 1 < len(parts):
                upload_uuid = parts[idx + 1]
        except Exception:
            pass

    if not upload_uuid:
        upload_uuid = str(uuid.uuid1())

    sample_id = upload_uuid

    status_store.update_status(upload_uuid, 0, "Wait for file check...")

    basename = os.path.splitext(os.path.basename(filenames_upload_image[0]))[0]

    print(f"DEBUG: Callback triggered for {upload_uuid}. Path: {filenames_upload_image[0]}")

    status_store.update_status(upload_uuid, 5, "Checking file availability...")
    print(f"DEBUG_CHECKPOINT: Checking vio.exists for {filenames_upload_image[0]}", flush=True)
    _wait_for_file(filenames_upload_image[0])

    print("DEBUG: File found. Starting processing.")
    status_store.update_status(upload_uuid, 20, "Processing Data Dimensions...")

    print(f"DEBUG_CHECKPOINT: Calling thor.process_data for {filenames_upload_image[0]}", flush=True)
    try:
        height, width = thor.process_data(sample_id, img_path=filenames_upload_image[0])
        print(f"DEBUG: process_data returned: {height}, {width}")
    except Exception as e:
        print(f"DEBUG: process_data FAILED: {e}")
        raise e

    args["heightWidth"] = [height, width]

    args['sampleId'] = sample_id
    args['fileName'] = basename
    args.pop("tutorialImagePath", None)
    dumpjson_parameter_from_user_input(folder_id, work_dir, args=args)

    files = files_generate(sample_id)

    src_path = filenames_upload_image[0]
    dst_path = vio.join_path(data_path, files["img"])
    print(f"DEBUG: Copying from {src_path} to {dst_path}")
    vio.copy(src_path, dst_path)

    # Build the WSI before deleting the local source to avoid another download.
    status_store.update_status(upload_uuid, 50, "Generating WSI Tiling...")
    print("DEBUG: Calling get_wsi...")
    get_wsi(folder_id, work_dir, local_img_path=src_path)

    status_store.update_status(upload_uuid, 80, "Cleaning temporary upload...")
    try:
        if src_path and os.path.exists(src_path):
            print(f"DEBUG: Removing original upload {src_path}")
            vio.remove(src_path)

        try:
            if upload_path and os.path.exists(upload_path):
                if not upload_path.startswith("s3://"):
                    os.remove(upload_path)
                    print(f"DEBUG: Deleted local data_input_temp file: {upload_path}")

                    parent_dir = os.path.dirname(upload_path)
                    if not os.listdir(parent_dir):
                        os.rmdir(parent_dir)
                        print(f"DEBUG: Removed empty parent folder: {parent_dir}")
        except Exception as e:
            print(f"WARNING: Failed to cleanup local file {upload_path}: {e}")

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
        _wait_for_file(source_path)

        spatial_dir = _spatial_omics_dir(work_dir, folder_id)
        vio.ensure_dir(spatial_dir)
        stored_path = vio.join_path(spatial_dir, "spatial_expression.h5ad")

        if os.path.splitext(source_path)[1].lower() != ".h5ad":
            raise ValueError("Upload expects a .h5ad file. Convert .h5 files first with src/convert_feature_slice_h5.py.")

        if job_id:
            status_store.update_status(job_id, 20, "Saving h5ad file...")
        # Chunk uploads and the workspace live on the same local filesystem.
        # An atomic move avoids a second full-size copy (the demo h5ad is ~1.3 GB),
        # which is both much faster and important on machines low on free space.
        os.replace(source_path, stored_path)

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

        if job_id:
            status_store.update_status(job_id, 70, "Saving h5ad metadata...")

        cluster_path = _spatial_omics_cluster_path(work_dir, folder_id)
        state.update({
            "cluster_path": cluster_path,
            "cluster_key": "spatial_cluster",
            "cluster_status": "running",
        })
        state_path = _spatial_omics_state_path(work_dir, folder_id)
        vio.dump_json(state, state_path, indent=2)

        if job_id:
            status_store.update_status(job_id, 80, "h5ad ready; clustering continues in background")

        _start_spatial_clustering_background(stored_path, cluster_path, state_path, job_id)

        summary_children = [
            html.Div("h5ad ready", className="omics-upload-title"),
            html.Div(className="omics-upload-stats", children=[
                html.Span(f"{n_obs:,} spots"),
                html.Span(f"{n_vars:,} genes"),
            ]),
            html.Div(
                "Basic clustering is running in the background. Re-visualize after it completes to see cluster overlay.",
                className="omics-upload-genes"
            ),
        ]
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
