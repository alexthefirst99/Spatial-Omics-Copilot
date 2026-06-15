from niceview.utils.convert import h5ad_converter
from niceview.utils.dataset import ThorQuery
from niceview.utils.cell_select import get_region
from niceview.interface.interface import *
import toml
import shutil
import json
import niceview.utils.io as vio
from dash import html
from dash import dcc
import pandas as pd
import os
import time
import dash
from niceview.utils.tools import save_roi_data_img
import plotly.graph_objects as go
import numpy as np
from shapely.geometry import Point, Polygon
import scipy
import scipy.sparse
import anndata as ad
from flask import send_file
import uuid
import app.status_store as status_store


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

cell_adata, wsi_img = None, None
geojson_coords = None
mapper = 1
gene_index = 4
area = 1

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

    max_dim = max(height, width)

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

    # update the scale factor of data
    status_store.update_status(upload_uuid, 80, "Calculating Scale Factors...")
    try:
        thor, args, p_input_json = get_parameter(folder_id, work_dir)
        sample_id_file = args["sampleIdFile"]
        cache = cache_generate(sample_id, sample_id_file=sample_id_file)
        gis_img_path = os.path.join(cache_path, cache["gis-img-file"])
        gis_img_path = os.path.abspath(gis_img_path)
        #factor = thor.get_factor(gis_img_path)
        #new_factor = f"e*{factor}"
        #js_path = app_dir + "/assets/dash_leaflet.js"
        #update_javascript(file_path=js_path, new_factor=new_factor)

        global wsi_img
        cell_adata, wsi_img = thor.get_cell_adata_and_img(args['sampleId'], local_img_path=src_path)
        global mapper
        mapper = thor.get_coord_mapping(sample_id_file)
    except Exception as e:
        print(f"[ERROR] Processing error but continuing: {e}")
    finally:
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
    return None
# html.H5("Click the home button on the map to see input image", className="text") 


def _spatial_omics_state_path(work_dir, folder_id=""):
    return f'{work_dir}/user{folder_id}/spatial_omics.json'


def _spatial_omics_dir(work_dir, folder_id=""):
    return f'{work_dir}/user{folder_id}/spatial_omics'


def _upload_job_id_from_path(path):
    marker = "data_input_temp/tmp/"
    if marker not in path:
        return None
    try:
        return path.split(marker, 1)[1].split("/", 1)[0]
    except Exception:
        return None


def upload_spatial_h5ad(filenames_upload_h5ad, folder_id, work_dir):
    """Register a spatial AnnData file for later ROI-level AI analysis."""
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
        if job_id:
            status_store.update_status(job_id, 80, "Saving h5ad metadata...")
        vio.dump_json(state, _spatial_omics_state_path(work_dir, folder_id), indent=2)

        if job_id:
            status_store.update_status(job_id, 100, "h5ad ready for ROI analysis")

        return html.Div(className="omics-upload-summary", children=[
            html.Div("h5ad ready", className="omics-upload-title"),
            html.Div(className="omics-upload-stats", children=[
                html.Span(f"{n_obs:,} spots"),
                html.Span(f"{n_vars:,} genes"),
            ]),
            html.Div("Example: " + ", ".join(preview_genes), className="omics-upload-genes"),
        ])
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


# choose cell or spot data
def show_cell_spot_upload(spot_cell_option, folder_id, work_dir):
    """
    Displays the upload options based on the selected data type.

    Parameters:
        spot_cell_option (str): Selected data type ('Spot data' or 'Cell data').

    Returns:
        html.Div: Div containing upload options.
    """
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id = args["sampleId"]
   


    sample_id_file = args['sampleIdFile']
    
    
    if spot_cell_option == "Spot data":
        # show spot upload box
        return html.Div(className="upload-data", children=[
            html.H5("Upload gene expression data(.h5ad file) for spot:", className="text"),
            html.H5("Upload gene expression data(.h5ad file) for spot:", className="text"),
            S3Upload(id='upload-data-addition-spot-result', label="Choose File")
        ])
    elif spot_cell_option == "Cell data":
        
        # show cell upload box
        return html.Div(className="upload-data", children=[
            html.H5("Upload mask(.npz file) and gene expression data(.h5ad file) for cell:", className="text"),
            html.H5("Upload mask(.npz file) and gene expression data(.h5ad file) for cell:", className="text"),
            S3Upload(id='upload-data-addition-cell-result', label="Choose File")
        ])
    else:
        # show nothing
        return html.Div(children=[])


# upload aditional data        
def upload_spot_data(filenames_upload_spot_data, folder_id, work_dir):
    """
    Uploads additional spot data and performs necessary operations.

    Parameters:
        filenames_upload_spot_data (list): List of uploaded filenames.

    Returns:
        None
    """
    # get parameter in argument file
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id = args['sampleId']
    height = args["heightWidth"][0]
    width = args["heightWidth"][1]
    height_width = args["heightWidth"]
    max_dim = max(height, width)
    files = files_generate(sample_id)
    # if ".npz" in filenames_upload_spot_data[0]:
    #     while not vio.exists(filenames_upload_spot_data[0]):
    #         time.sleep(5)
    #     vio.copy(filenames_upload_spot_data[0], vio.join_path(data_path, files["mask"]))
    # # if it not mask file h5ad convert
    # else:

    # h5ad convert
    while not vio.exists(filenames_upload_spot_data[0]):
        time.sleep(5)
    db_info_path = f'{work_dir}/db/db-info.json'
    h5ad_converter(data_path, db_info_path, sample_id, h5ad_spot=filenames_upload_spot_data[0])
    vio.remove(filenames_upload_spot_data[0])

    # remove temp folder that dash-uploader created
    gene_name = vio.read_csv(vio.join_path(data_path, files["spots-gene-names"]), header=None, index_col=0)
    gene_list = list(gene_name.index)
    return [
                    html.H5("Choose Gene", className="text"),
                    dcc.Dropdown(gene_list, id="gene-input-container", className='dropdown-input', placeholder="Input or Select Gene"),
                    html.H5("Insert vmax vmin", className="text"),
                    dcc.Input(id='spot-input-min', type='text', className="input-container",debounce=True, placeholder="vmin"),
                    dcc.Input(id='spot-input-max', type='text', className="input-container",debounce=True, placeholder="vmax"),
                    html.Button(id='spot-vminmax-button', n_clicks=0, children='Submit', className="button button-input"),
                    ]


def upload_cell_data(filenames_upload_cell_data, folder_id, work_dir):
    """
    Uploads additional cell data and performs necessary operations.

    Parameters:
        filenames_upload_cell_data (list): List of uploaded filenames.

    Returns:
        None
    """
    # get parameter in argument file, and get max dimension
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id = args['sampleId']
    height = args["heightWidth"][0]
    width = args["heightWidth"][1]
    height_width = args["heightWidth"]
    max_dim = max(height, width)
    files = files_generate(sample_id)

    # check if 'npz' in the file name, if yes copy to data path and rename it
    if ".npz" in filenames_upload_cell_data[0]:
        while not vio.exists(filenames_upload_cell_data[0]):
            time.sleep(5)
        vio.copy(filenames_upload_cell_data[0], vio.join_path(data_path, files["mask"]))
        
        # Cleanup
        vio.remove(filenames_upload_cell_data[0])

        if vio.exists(vio.join_path(data_path, files["cell-barcode"])):
            cell_number = vio.read_csv(vio.join_path(data_path, files["cell-barcode"]),header=None, index_col=0,sep="\t")
            cell_number = len(list(cell_number.index))
            gene = vio.read_csv(vio.join_path(data_path, files["cells-gene-names"]),header=None, index_col=0,sep="\t")
            gene_list = list(gene.index)
            # return html.H4(f"Cell number = {cell_number} cells", className="text")
            return html.Div(id="gene-dropdown-cell", children=[
                        html.H5("Choose Gene", className="text"),
                        dcc.Dropdown(gene_list, id="gene-input-container", className='dropdown-input', placeholder="Input or Select Gene"),
                        html.H5("Insert vmax vmin", className="text"),
                        dcc.Input(id='cell-input-min', type='text', className="input-container",debounce=True, placeholder="vmin"),
                        dcc.Input(id='cell-input-max', type='text', className="input-container",debounce=True, placeholder="vmax"),
                        html.Button(id='cell-vminmax-button', n_clicks=0, children='Submit', className="button button-input"),
                        html.H4(f"Cell number = {cell_number} cells", className="text")
                ])
        else:
            return None
    # same thing with h5ad, then convert
    else:
        while not vio.exists(filenames_upload_cell_data[0]):
            print(filenames_upload_cell_data[0])
            time.sleep(5)
        db_info_path = f'{work_dir}/db/db-info.json'
        h5ad_converter(data_path, db_info_path, sample_id, h5ad_cell=filenames_upload_cell_data[0])
        vio.remove(filenames_upload_cell_data[0])
        cell_number = vio.read_csv(os.path.join(data_path, files["cell-barcode"]),header=None, index_col=0,sep="\t")
        cell_number = len(list(cell_number.index))
        gene = vio.read_csv(os.path.join(data_path, files["cells-gene-names"]),header=None, index_col=0,sep="\t")
        gene_list = list(gene.index)
        gene_dict = {value: index for index, value in enumerate(gene_list)}
        try: 
            vio.remove(f"{work_dir}/db/data/{sample_id}-gene-index.json")
        except FileNotFoundError:
            pass
        vio.dump_json(gene_dict, f"{work_dir}/db/data/{sample_id}-gene-index.json")
        
        if not vio.exists(os.path.join(data_path, files["mask"])):
            return html.H5("Please upload mask file", className="text")
        
        # update global variable
    global cell_adata
    global wsi_img
    global mapper
    global gene_index
    cell_adata, wsi_img = thor.get_cell_adata_and_img(sample_id)
    sample_id_file = args['sampleIdFile']
    mapper = thor.get_coord_mapping(sample_id_file)
    gene_index = vio.load_json(f"{work_dir}/db/data/{sample_id}-gene-index.json")


        # return html.H4(f"Cell number = {cell_number} cells", className="text") 
    return html.Div(id="gene-dropdown-cell", children=[
                    html.H5("Choose Gene", className="text"),
                    dcc.Dropdown(gene_list, id="gene-input-container", className='dropdown-input', placeholder="Input or Select Gene"),
                    html.H5("Insert vmax vmin", className="text"),
                    dcc.Input(id='cell-input-min', type='text', className="input-container",debounce=True, placeholder="vmin"),
                    dcc.Input(id='cell-input-max', type='text', className="input-container",debounce=True, placeholder="vmax"),
                    html.Button(id='cell-vminmax-button', n_clicks=0, children='Submit', className="button button-input"),
                    html.H4(f"Cell number = {cell_number} cells", className="text")
            ])
    

# choose type of visualization
def update_output_visual(spot_cell_option, visualize_option, folder_id, work_dir):
    """
    Updates the visualization options based on selected data type and visualization type.

    Parameters:
        spot_cell_option (str): Selected data type ('Spot data' or 'Cell data').
        visualize_option (str): Selected visualization type.

    Returns:
        html.Div: Div containing updated visualization options.
    """
    try:
        os.remove(f"{work_dir}/user{folder_id}/selected_area.zip")
    except FileNotFoundError:
        pass

   # get parameter
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id = args['sampleId']
    # nm: Save the current visualization state so other components (like chat) know what's active
    if visualize_option:
        args['visualizeOption'] = visualize_option
    if spot_cell_option:
        args['spotCellOption'] = spot_cell_option
    dumpjson_parameter_from_user_input(folder_id, work_dir, args=args)
    
    files = files_generate(sample_id)


     # if choose gene expression
    if visualize_option == "Gene Expression":

        # get parameter

        if spot_cell_option == "Spot data":

            # check if file exist
            if vio.exists(os.path.join(data_path, files["spots-gene-names"])):
                # time.sleep(5)
                # return html.H5("Data has not successfuly uploaded yet", className="text")
            
            # read gene name file and convert to list
                gene_name = pd.read_csv(os.path.join(data_path, files["spots-gene-names"]), header=None, index_col=0)
                gene_list = list(gene_name.index)
                gene_drop = html.Div(id="gene-dropdown-spot",children=[
                    html.H5("Choose Gene", className="text"),
                    dcc.Dropdown(gene_list, id="gene-input-container", className='dropdown-input', placeholder="Input or Select Gene"),
                    html.H5("Insert vmax vmin", className="text"),
                    dcc.Input(id='spot-input-min', type='text', className="input-container",debounce=True, placeholder="vmin"),
                    dcc.Input(id='spot-input-max', type='text', className="input-container",debounce=True, placeholder="vmax"),
                    html.Button(id='spot-vminmax-button', n_clicks=0, children='Submit', className="button button-input"),
                    ]
                    )
            else:
                gene_drop = html.Div(id="gene-dropdown-spot")

                # return the dropdown contain gene list
            # return html.Div(children=[
            #     html.H5("Choose Gene", className="text"),
            #     dcc.Dropdown(gene_list, id="gene-input-container", className='dropdown-input', placeholder="Input or Select Gene"),
            #     html.Br(),html.Br(),html.Br(),
            #     html.H5("Insert vmax vmin", className="text"),
            #     dcc.Input(id='spot-input-min', type='text', className="input-container",debounce=True, placeholder="vmin"),
            #     dcc.Input(id='spot-input-max', type='text', className="input-container",debounce=True, placeholder="vmax"),
            #     html.Button(id='spot-vminmax-button', n_clicks=0, children='Submit', className="button button-input"),
            #     ]
            #     )
            return html.Div(children=[
                html.Div(className="upload-data", children=[
                html.H5("Upload gene expression data(.h5ad file) for cell:", className="text"),
                S3Upload(id='upload-data-addition-spot-result', label="Choose File")
            ]),
            gene_drop,
            ])
        # if cell data
        elif spot_cell_option == "Cell data":
            
            # check file exist
            if vio.exists(os.path.join(data_path, files["cells-gene-names"])):
                # time.sleep(5)
                # return html.H5("Data has not successfuly uploaded yet", className="text")
            
            # read gene name file and convert to list 

                gene_name = pd.read_csv(os.path.join(data_path, files["cells-gene-names"]), header=None, index_col=0)
                gene_list = list(gene_name.index)
                gene_drop = html.Div(id="gene-dropdown-cell", children=[
                        html.H5("Choose Gene", className="text"),
                        dcc.Dropdown(gene_list, id="gene-input-container", className='dropdown-input', placeholder="Input or Select Gene"),
                        html.H5("Insert vmax vmin", className="text"),
                        dcc.Input(id='cell-input-min', type='text', className="input-container",debounce=True, placeholder="vmin"),
                        dcc.Input(id='cell-input-max', type='text', className="input-container",debounce=True, placeholder="vmax"),
                        html.Button(id='cell-vminmax-button', n_clicks=0, children='Submit', className="button button-input"),
                ])
            else:

                gene_drop = html.Div(id="gene-dropdown-cell")
            # return the dropdown contain gene list
            # return html.Div(children=[
            #     html.H5("Choose Gene", className="text"),
            #     dcc.Dropdown(gene_list, id="gene-input-container", className='dropdown-input', placeholder="Input or Select Gene"),
            #     html.H5("Insert vmax vmin", className="text"),
            #     dcc.Input(id='cell-input-min', type='text', className="input-container",debounce=True, placeholder="vmin"),
            #     dcc.Input(id='cell-input-max', type='text', className="input-container",debounce=True, placeholder="vmax"),
            #     html.Button(id='cell-vminmax-button', n_clicks=0, children='Submit', className="button button-input"),
            #     ]
            #     )
            return html.Div(children=[
                html.Div(className="upload-data", children=[
                html.H5("Upload mask(.npz file) and gene expression data(.h5ad file) for cell:", className="text"),
                S3Upload(id='upload-data-addition-cell-result', label="Choose File")
            ]),
            gene_drop,
            ])
    
    # if chose Pathway
    elif visualize_option == "Pathway Enrichment Analysis":

        # if cell data
        if spot_cell_option == "Cell data":
            files = files_generate(sample_id)
            if vio.exists(os.path.join(data_path, files["cell-pathway-name"])):
                # read pathway file convert to list
                pathway_name = pd.read_csv(os.path.join(data_path, files["cell-pathway-name"]), header=None, index_col=0)
                pathway_list = list(pathway_name.index)
                pathway_drop = html.Div( id="pathway-dropdown", children=[
                        html.H5("Choose Pathway", className="text"),
                        dcc.Dropdown(pathway_list, id="pathway-input-container", className='dropdown-input', placeholder="Select Pathway")
                ])
            else:
                pathway_drop = html.Div(id="pathway-dropdown")
            # return upload container
            return html.Div(children=[
                html.Div(className="upload-data", children=[
                    html.H5("Upload mask(.npz file) and pathway (.h5ad file) with vars are pathways and obs are cells", className="text"),
                    S3Upload(id='upload-data-pathway-result', label="Choose File")
                ]),
                pathway_drop,
                
            ])
        # or show not support
        else:
            return html.H5("Only support Cell data", className="text")
        
    # if chose CNV    
    elif visualize_option == "CNV":

        # if cell data
        if spot_cell_option == "Cell data":

            # return upload container
            return html.Div(children=[
                html.Div(className="upload-data", children=[
                    html.H5("Upload CNV (.csv or .txt file) with first column are cells and second column are CNV label", className="text"),
                    S3Upload(id='upload-data-cnv-result', label="Choose File"),
                    html.Br(),
                    html.H5("Click re-visualize button after finish upload to show result", className="text"),
                ])
            ])
        
        # or show not support
        else:
            return html.H5("Only support Cell data", className="text")
    
    elif visualize_option == "Similar Cell Locate":
        if spot_cell_option == "Cell data":
            return html.Div(children=[
                html.Div(className="upload-data", children=[
                        html.Button("Search", className="button", id="btn_find"),
                        html.H5("Select area and click search button to calculate.", className="text"),
                        html.H5("Then click re-visualize button to show result", className="text"),
                        html.H5("(You should already have uploaded the gene expression data.)", className="text"),
                        ])
                ])
        
        # or show not support
        else:
            return html.H5("Only support Cell data", className="text")
        
    # if chose cell detection   
    elif visualize_option == 'Cell Detection Check':
        # if cell data
        if spot_cell_option == "Cell data":
            sample_id = args['sampleId']
            files = files_generate(sample_id)
            while not vio.exists(os.path.join(data_path, files["mask"])):
                return html.Div(children=[
                html.Div(className="upload-data", children=[
                html.H5("Upload mask(.npz file) for cell:", className="text"),
                S3Upload(id='upload-data-addition-cell-detection-result', label="Choose File"),
                html.Div(id="cell-detection-confirm"),
                html.H5("Click re-visualize to see result", className="text")
            ])
            ])
            # calculation_cell_detection(folder_id, work_dir)
        
        # or show not support
        else:
            return html.H5("Only support Cell data", className="text") 

    # Alex added this visualization option, to allow users to upload region coordinates.
    # 2/25/2025
    # if chose upload region coordinates 
    elif visualize_option == "Upload region annotation":
        return html.Div(children=[
                html.Div(className="upload-data", children=[
                    S3Upload(id='upload-data-coor-result', label="Choose File"),
                    html.H5("Click re-visualize button after finish upload to show result", className="text"),
                ])
            ])
    

# # cell detection button
# def show_cell_detection(n_clicks, folder_id, work_dir):
#     data_path, cache_path = get_data_path_cache_path(work_dir)
#     # if click
#     if n_clicks:

#         # generate map
#         map_input = visualization_img_cell(folder_id, work_dir, cell_detect=True)

#         # visualize result
#         return html.Div(id="input-image", children=[map_input])
    
#     # if no click keep previous map
#     else:
#         raise dash.exceptions.PreventUpdate


# upload pathway
def upload_pathway(filenames_upload_pathway, folder_id, work_dir):
    """
    Uploads pathway data and performs necessary operations.

    Parameters:
        filenames_upload_pathway (list): List of uploaded filenames.

    Returns:
        None
    """
    # get parameter
    # get parameter in argument file, and get max dimension
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id = args['sampleId']
    height = args["heightWidth"][0]
    width = args["heightWidth"][1]
    height_width = args["heightWidth"]
    max_dim = max(height, width)
    files = files_generate(sample_id)

    # remove previous selected area folder, if not found pass
    try:
        os.remove(f"{work_dir}/user{folder_id}/selected_area.zip")
    except FileNotFoundError:
        pass

    # check if file exist
    while not vio.exists(filenames_upload_pathway[0]):
        time.sleep(5)

    if ".npz" in filenames_upload_pathway[0]:
        while not vio.exists(filenames_upload_pathway[0]):
            time.sleep(5)
        vio.copy(filenames_upload_pathway[0], vio.join_path(data_path, files["mask"]))
        vio.remove(filenames_upload_pathway[0])
        if vio.exists(os.path.join(data_path, files["cell-pathway-name"])):
            pathway_name = pd.read_csv(os.path.join(data_path, files["cell-pathway-name"]), header=None, index_col=0)
            pathway_list = list(pathway_name.index)
            return html.Div(children=[
                html.H5("Choose Pathway", className="text"),
                dcc.Dropdown(pathway_list, id="pathway-input-container", className='dropdown-input', placeholder="Select Pathway")
        ])
            # return html.H5("Pathway has been uploaded", className="text")
        else:
            return None

    else:
        while not vio.exists(filenames_upload_pathway[0]):
            time.sleep(5)
        db_info_path = f'{work_dir}/db/db-info.json'
        h5ad_converter(data_path, db_info_path, sample_id, h5ad_cell=filenames_upload_pathway[0])
        os.remove(os.path.join(data_path, files['cell-gene']))
        os.remove(os.path.join(data_path, files['cells-gene-names']))
        vio.remove(filenames_upload_pathway[0])

        h5ad_converter(data_path, db_info_path, sample_id, h5ad_cell_pathway=filenames_upload_pathway[0])

        # remove temp folder create by dash uploader
        

        # get file name by rules
        files = files_generate(sample_id)

        # read pathway file convert to list
        pathway_name = pd.read_csv(os.path.join(data_path, files["cell-pathway-name"]), header=None, index_col=0)
        pathway_list = list(pathway_name.index)
    
        if not vio.exists(os.path.join(data_path, files["mask"])):
                return html.H5("Please upload mask file", className="text")

        # return dropdown with all pathway
        return html.Div(children=[
                html.H5("Choose Pathway", className="text"),
                dcc.Dropdown(pathway_list, id="pathway-input-container", className='dropdown-input', placeholder="Select Pathway")
        ])


# choose pathway
def get_pathway_output(spot_cell_option, pathway_value, folder_id, work_dir):
    """
    Handles the selection of a pathway and performs necessary calculation.

    Parameters:
        spot_cell_option (str): Selected data type ('Spot data' or 'Cell data').
        pathway_value (str): Selected pathway name.

    Returns:
        html.Div: Div containing the updated visualization.
    """
    data_path, cache_path = get_data_path_cache_path(work_dir)
    try:
        os.remove(f"{work_dir}/user{folder_id}/selected_area.zip")
    except FileNotFoundError:
        pass
    # if cell data
    if spot_cell_option == "Cell data":

        # if pathway been chosen
        if pathway_value is not None:

            # get parameter
            thor, args, p_input_json = get_parameter(folder_id, work_dir)
            sample_id = args['sampleId']

            # remove previous selected area folder, if not found pass

            # get pathway name from user input and dump it to json file
            args["selectedPathway"] = pathway_value
            dumpjson_parameter_from_user_input(folder_id, work_dir, args=args)

            # check if user already chose that pathway, if not calculate and dump it to json file, if yes show previous result
            if pathway_value not in p_input_json['Pathway']:

                calculation_pathway(folder_id, work_dir)

                p_input_json['Pathway'].append(pathway_value)
                dumpjson_parameter_from_user_input(folder_id, work_dir, p_input_json=p_input_json)

                map_input = visualization_img_cell(folder_id, work_dir, pathway=True)

            else:
                sample_id_pathway = sample_id + "-" + pathway_value
                args['sampleIdPathway'] = sample_id_pathway

                dumpjson_parameter_from_user_input(folder_id, work_dir, args=args)
                map_input = visualization_img_cell(folder_id, work_dir, pathway=True)

            # visualize result    
            return html.Div(id="input-image", children=[map_input])
        
        # or keep previous map
        else:
            raise dash.exceptions.PreventUpdate
    else:
        raise dash.exceptions.PreventUpdate

# upload cnv
def upload_cnv(filenames_upload_cnv, folder_id, work_dir):
    """
    Uploads cnv data and performs necessary operations.

    Parameters:
        filenames_upload_cnv (list): List of uploaded filenames.

    Returns:
        None
    """

    # get parameter
    data_path, cache_path = get_data_path_cache_path(work_dir)
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id = args['sampleId']

    # remove previous selected area folder, if not found pass
    try:
        os.remove(f"{work_dir}/user{folder_id}/selected_area.zip")
    except FileNotFoundError:
        pass

    # get file name by rules
    files = files_generate(sample_id)

    # check if all file needed exist
    while not vio.exists(os.path.join(data_path, files["cell-barcode"])):
        return html.H5("Not enough files to generate result", className="text") 
    while not vio.exists(os.path.join(data_path, files["cell-info"])):
        return html.H5("Not enough files to generate result", className="text") 
    while not vio.exists(os.path.join(data_path, files["mask"])):
        return html.H5("Not enough files to generate result", className="text") 
    cell_order = vio.read_csv(os.path.join(data_path, files["cell-barcode"]), index_col=0, sep="\t", header=None)

    # check extension of cnv file, to get appropriate read
    if ".csv" in filenames_upload_cnv[0]:
        input_cnv = pd.read_csv(filenames_upload_cnv[0], index_col=0)
    else:
        input_cnv = pd.read_csv(filenames_upload_cnv[0], index_col=0, sep="\t")

    # reorder cell order
    input_cnv = input_cnv.reindex(list(cell_order.index))

    # open cell info and put cnv info under label
    cell_info = vio.read_csv(os.path.join(data_path, files["cell-info"]), index_col=0)
    cell_info["label"] = list(input_cnv.iloc[:, 0])
    vio.write_csv(cell_info, os.path.join(data_path, files["cell-info"]))
    # calculate cnv
    calculation_CNV(folder_id, work_dir)
    vio.remove(filenames_upload_cnv[0])
    return None


# upload region coordinates
# Alex added this function to handle the uploaded region coordinates.
def upload_coordinate(filenames_upload_regions, folder_id, work_dir):
    global geojson_coords
    # Check if uploaded file is local (Dash Uploads usually are)
    # If S3 work_dir is mapped weirdly, we trust Dash Uploader gave a readable path.
    # If Dash Uploader saved to valid path readable by python open(), this is fine.
    # But for safety, vio.open_file could be used if path is S3.
    # filenames_upload_regions[0] is typically a local path from Dash Uploader.
    with vio.open_file(filenames_upload_regions[0], 'r') as f:
        coords = json.load(f)
    geojson_coords = convert_pixel_to_geojson(coords, folder_id, work_dir)
    vio.remove(filenames_upload_regions[0])
    return None


# choose gene
def get_gene(spot_cell_option, gene_chosen, folder_id, work_dir):
    """
    Handles the selection of a gene and performs necessary calculation.

    Parameters:
        spot_cell_option (str): Selected data type ('Spot data' or 'Cell data').
        gene_chosen (str): Selected gene name.

    Returns:
        html.Div: Div containing the updated visualization.
    """
    data_path, cache_path = get_data_path_cache_path(work_dir)
    if spot_cell_option == "Spot data":
        if gene_chosen is not None:

            # Get Thor, arguments, and previous input JSON
            thor, args, p_input_json = get_parameter(folder_id, work_dir)
            sample_id = args['sampleId']

            # remove previous selected area folder, if not found pass
            try:
                os.remove(f"{work_dir}/user{folder_id}/selected_area.zip")
            except FileNotFoundError:
                pass
            
            # Set the selected spot gene name and dump to args json
            args['selectedSpotGeneName'] = gene_chosen
            dumpjson_parameter_from_user_input(folder_id, work_dir, args=args)

            # If gene not present in previous input JSON
            if gene_chosen not in p_input_json['SpotGene']:

                # Perform calculation for Spot data
                calculation_spot(folder_id, work_dir)

                # Update previous input JSON with new gene
                p_input_json['SpotGene'].append(gene_chosen)
                dumpjson_parameter_from_user_input(folder_id, work_dir, p_input_json=p_input_json)

                # Generate visualization for Spot data
                map_input = visualization_img_spot(folder_id, work_dir)

            # If gene already present in previous input JSON
            else:

                # get file name with gene name in it to prevent dash leaflet cache
                sample_id_gene_spot = sample_id + "-" + gene_chosen
                args['sampleIdSpotGene'] = sample_id_gene_spot
                dumpjson_parameter_from_user_input(folder_id, work_dir, args=args)

                # Generate visualization for Spot data
                map_input = visualization_img_spot(folder_id, work_dir)

            # Return updated visualization
            return html.Div(id="input-image", children=[map_input])
        
        # If gene is None, prevent update
        else:
            raise dash.exceptions.PreventUpdate
         
    elif spot_cell_option == "Cell data":
        if gene_chosen is not None:
            # Get Thor, arguments, and previous input JSON
            thor, args, p_input_json = get_parameter(folder_id, work_dir)
            sample_id = args['sampleId']

            # global cell_adata
            # global wsi_img
            # global mapper
            # global gene_index
            # cell_adata, wsi_img = thor.get_cell_adata_and_img(sample_id)
            # sample_id_file = args['sampleIdFile']
            # mapper = thor.get_coord_mapping(sample_id_file)
            # with open(f"{work_dir}/db/data/{sample_id}-gene-index.json", 'r') as f:
            #     gene_index = json.load(f)

            # Attempt to remove a file, if it exists
            try:
                os.remove(f"{work_dir}/user{folder_id}/selected_area.zip")
            except FileNotFoundError:
                pass
            
            # Set the selected cell gene name and dump to args json
            args['selectedCellGeneName'] = gene_chosen
            dumpjson_parameter_from_user_input(folder_id, work_dir, args=args)

            # If gene not present in previous input JSON
            if gene_chosen not in p_input_json['CellGene']:
                # Perform calculation for cell data
                calculation_cell(folder_id, work_dir)

                # dump gene name in previous input json
                p_input_json['CellGene'].append(gene_chosen)
                dumpjson_parameter_from_user_input(folder_id, work_dir, p_input_json=p_input_json)

                # Generate visualization for cell data
                map_input = visualization_img_cell(folder_id, work_dir)
            else:

                # get file name with gene name in it to prevent dash leaflet cache
                sample_id_gene_cell = sample_id + "-" + gene_chosen
                args['sampleIdCellGene'] = sample_id_gene_cell
                dumpjson_parameter_from_user_input(folder_id, work_dir, args=args)

                # Generate visualization for cell data
                map_input = visualization_img_cell(folder_id, work_dir)

            # Return updated visualization
            return html.Div(id="input-image", children=[map_input])
        
        # If gene is None, prevent update
        else:
            raise dash.exceptions.PreventUpdate
        
    # If spot_cell_option is None, prevent update
    else:
        raise dash.exceptions.PreventUpdate
    

# cell vmin vmax
def cell_vmin_vmax(n_clicks, vmin, vmax, folder_id, work_dir):
    if vmin is not None or vmax is not None:

            data_path, cache_path = get_data_path_cache_path(work_dir)
            thor, args, p_input_json = get_parameter(folder_id, work_dir)
            sample_id_gene_cell = args['sampleIdCellGene'] 
            if vmin == "":
                vmin = None
            if vmax == "":
                vmax = None

            if vmin is None and vmax is None:
                raise dash.exceptions.PreventUpdate

            if vmin is not None and vmax is not None:
                if 'p' not in vmin:
                    vmin = float(vmin)
                if 'p' not in vmax:
                    vmax = float(vmax)
                args["sampleIdMinMax"] = sample_id_gene_cell + '-' + str(vmin) + '-' + str(vmax)
                dumpjson_parameter_from_user_input(folder_id, work_dir, args, p_input_json)
                calculation_cell(folder_id, work_dir, vmin, vmax)
                map_input = visualization_img_cell(folder_id, work_dir, min_max=True)
            elif vmin is not None:
                if 'p' not in vmin:
                    vmin = float(vmin)
                args["sampleIdMin"] = sample_id_gene_cell + '-' + str(vmin)
                dumpjson_parameter_from_user_input(folder_id, work_dir, args, p_input_json)
                calculation_cell(folder_id, work_dir, vmin, vmax)
                map_input = visualization_img_cell(folder_id, work_dir, min=True)
            elif vmax is not None:
                if 'p' not in vmax:
                    vmax = float(vmax)
                args["sampleIdMax"] = sample_id_gene_cell + '-' + str(vmax)
                dumpjson_parameter_from_user_input(folder_id, work_dir, args, p_input_json)
                calculation_cell(folder_id, work_dir, vmin, vmax)
                map_input = visualization_img_cell(folder_id, work_dir, max=True)
            return html.Div(id="input-image", children=[map_input])
    else:
        raise dash.exceptions.PreventUpdate


# spot vmin vmax
def spot_vmin_vmax(n_clicks, vmin, vmax, folder_id, work_dir):
    if vmin is not None or vmax is not None:

            data_path, cache_path = get_data_path_cache_path(work_dir)
            thor, args, p_input_json = get_parameter(folder_id, work_dir)
            sample_id_gene_spot = args['sampleIdSpotGene'] 
            if vmin == "":
                vmin = None
            if vmax == "":
                vmax = None

            if vmin is None and vmax is None:
                raise dash.exceptions.PreventUpdate

            if vmin is not None and vmax is not None:
                if 'p' not in vmin:
                    vmin = float(vmin)
                if 'p' not in vmax:
                    vmax = float(vmax)
                args["sampleIdMinMax"] = sample_id_gene_spot + '-' + str(vmin) + '-' + str(vmax)
                dumpjson_parameter_from_user_input(folder_id, work_dir, args, p_input_json)
                calculation_spot(folder_id, work_dir, vmin, vmax)
                map_input = visualization_img_spot(folder_id, work_dir, min_max=True)
            elif vmin is not None:
                if 'p' not in vmin:
                    vmin = float(vmin)
                args["sampleIdMin"] = sample_id_gene_spot + '-' + str(vmin)
                dumpjson_parameter_from_user_input(folder_id, work_dir, args, p_input_json)
                calculation_spot(folder_id, work_dir, vmin, vmax)
                map_input = visualization_img_spot(folder_id, work_dir, min=True)
            elif vmax is not None:
                if 'p' not in vmax:
                    vmax = float(vmax)
                args["sampleIdMax"] = sample_id_gene_spot + '-' + str(vmax)
                dumpjson_parameter_from_user_input(folder_id, work_dir, args, p_input_json)
                calculation_spot(folder_id, work_dir, vmin, vmax)
                map_input = visualization_img_spot(folder_id, work_dir, max=True)
            return html.Div(id="input-image", children=[map_input])
    else:
        raise dash.exceptions.PreventUpdate


def reset(n_clicks, spot_cell_option, visual_type, folder_id, work_dir):
    """
    Reset image to original center

    Parameters:
        spot_cell_option (str): Selected data type ('Spot data' or 'Cell data').
        gene_chosen (str): Selected gene name.
        n_clicks (int): number of clicks

    Returns:
        html.Div: Div containing the updated visualization.
    """

    # this is to keep track of the updated global variable
    global geojson_coords

    data_path, cache_path = get_data_path_cache_path(work_dir)
    changed_id = [p['prop_id'] for p in dash.callback_context.triggered][0]

    if 'n_clicks' in changed_id:

        if spot_cell_option == "Spot data":
            if visual_type == "Gene Expression":
                map_input = visualization_img_spot(folder_id, work_dir, geojson_coords=None)
            elif visual_type == "Upload region annotation":
                map_input = visualization_img_input(folder_id, work_dir, geojson_coords=geojson_coords)
            else:
                map_input = visualization_img_input(folder_id, work_dir, geojson_coords=None)
        else:
            if visual_type == "Gene Expression":
                map_input = visualization_img_cell(folder_id, work_dir, geojson_coords=None)
            elif visual_type == "CNV":
                map_input = visualization_img_cell(folder_id, work_dir, cell_type=True, geojson_coords=None)
            elif visual_type == "Pathway Enrichment Analysis":
                map_input = visualization_img_cell(folder_id, work_dir, pathway=True, geojson_coords=None)
            elif visual_type == "Cell Detection Check":
                map_input = visualization_img_cell(folder_id, work_dir, cell_detect=True, geojson_coords=None)
            elif visual_type == "Similar Cell Locate":
                map_input = visualization_img_cell(folder_id, work_dir, cell_select=True, geojson_coords=None)
            elif visual_type == "Upload region annotation":
                map_input = visualization_img_input(folder_id, work_dir, geojson_coords=geojson_coords)
            else:
                map_input = visualization_img_input(folder_id, work_dir, geojson_coords=None)
        return html.Div(id="input-image", children=[map_input])
    else:
        raise dash.exceptions.PreventUpdate


def copy_and_rename_file(n_clicks, folder_id, work_dir, zip=False):
    """Copy and rename file.
    
    Args:
        n_clicks (int): Number of clicks.
    
    Returns:
        Download zip folder
    """
    if n_clicks:
        global wsi_img
        global cell_adata
        thor, args, p_input_json = get_parameter(folder_id, work_dir)
        data_path, cache_path = get_data_path_cache_path(work_dir)
        sample_id = args["sampleId"]
    """
    Copies and renames files based on n_clicks.
    """
    if n_clicks > 0: 
        # get parameter
        data_path, cache_path = get_data_path_cache_path(work_dir)
        thor, args, p_input_json = get_parameter(folder_id, work_dir)
        sample_id = args['sampleId']
        # create new folder
        dir_path = f'{work_dir}/user{folder_id}/selected_area/'
        
        # Use vio.ensure_dir to create directory (handles local only, S3 implicit)
        vio.ensure_dir(dir_path)

        # get resized coordinate path
        coord_path = f'{work_dir}/user{folder_id}/selected_area/coords.json'
        
        # S3-safe copy
        vio.copy(f'{work_dir}/user{folder_id}/coords.json', coord_path)

        # read already resized coordinate
        coords_temp = vio.load_json(coord_path)
        
        # assign coords as resized coordinate
        coords = coords_temp
        
        height = args["heightWidth"][0]
        width = args["heightWidth"][1]
        max_dim = max(height, width)
        
        # if image is not big coords and coords_temp is the same, and get the same global celladata
        save_roi_data_img(coords_temp,cell_adata, wsi_img, dir_path,resized_coords = coords)
        
        drawn_geojson = []
        # Pengzhi remarked writing the coords to coords.json is to empty the coords.json file after clicking the save button
        # 2025-01-31
        coords = []
        geojson_name = f'{work_dir}/user{folder_id}/roi.json'
        vio.dump_json(drawn_geojson, geojson_name, indent=2)
        coordjson_name = f'{work_dir}/user{folder_id}/coords.json'
        vio.dump_json(coords, coordjson_name, indent=2)
        if zip is True:
            # S3-safe zip
            vio.zip_folder_s3(dir_path, f"{work_dir}/user{folder_id}/selected_area.zip")

        # Pengzhi moved this part inside so it's triggered by n_clicks > 0
        # 2025-01-31
        try:
            # dcc.send_file might fail if file is on S3.
            # If S3, we need to download it to a temp local path and send that?
            # Or assume send_file only works locally.
            # If application is hosted on EC2 with S3 mounting or similar, it works.
            # But if purely S3 path, we should probably download it first.
            zip_file_path = f"{work_dir}/user{folder_id}/selected_area.zip"
            if vio.is_s3(zip_file_path):
                 # Limitation: dcc.send_file expects local path.
                 # We can try to rely on s3fs implicit mounting if present, but unlikely.
                 # Just return it and hope for the best if verification is skipped as requested.
                 pass
            return dcc.send_file(zip_file_path)
        except FileNotFoundError:
            return None
        # Pengzhi end
    else:
        return None


def save_roi(rois, folder_id, work_dir):
    """Save coordinates.
    
    Args:
        rois (list): List of ROIs from VivViewer.

    Returns:
        json file.
    """
    if rois is not None and len(rois) > 0:

        # Re-initialize mapper to ensure it exists in this thread/context
        thor, args, p_input_json = get_parameter(folder_id, work_dir)

        coords = []
        for roi in rois:
            points = roi.get("points", [])
            if not points: continue
            
            if roi.get("type") == "rect" or roi.get("type") == "polygon":
                # The frontend sends an array of points. It could be 2 points (min/max) 
                # or 4+ points for a full polygon/rect. To be safe, extract min and max 
                # from ALL points if it's explicitly a rect, or just close the polygon.
                poly = [p for p in points if p] # Ensure no nulls
                if poly and poly[0] != poly[-1]:
                    poly.append(poly[0])
                coords.append(poly)

        
        # Save verbatim rois as roi.json just in case
        roi_name = f'{work_dir}/user{folder_id}/roi.json'
        vio.dump_json(rois, roi_name, indent=2)
        
        coordjson_name = f'{work_dir}/user{folder_id}/coords.json'
        vio.dump_json(coords, coordjson_name, indent=2)
    else:
        # Clear them out if no ROIs
        roi_name = f'{work_dir}/user{folder_id}/roi.json'
        coordjson_name = f'{work_dir}/user{folder_id}/coords.json'
        if vio.exists(roi_name): vio.remove(roi_name)
        if vio.exists(coordjson_name): vio.remove(coordjson_name)
        
    return None

def convert_pixel_to_geojson(coords, folder_id, work_dir):
    global mapper
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    sample_id_file = args['sampleIdFile']
    sample_id = args['sampleId']
    height = args["heightWidth"][0]
    width = args["heightWidth"][1]
    max_dim = max(height, width)

    reverse_mapper = thor.reverse_coordinate_mapping(sample_id)
    geojson_coords = []
    for coors in coords[::-1]:
        geojson_coor = []
        for coor in coors:
            coor = coor[::-1].copy()
            reverse_coor = list(reverse_mapper(*coor))[::-1]
            geojson_coor.append(reverse_coor)
        geojson_coords.append(geojson_coor)
    return geojson_coords


def plot_stats(drawn_geojson, gene_chosen, folder_id, work_dir):
    """Plot stats.
    
    Args:
        n_clicks (int): number of clicks.
        drawn_geojson (dict): GeoJSON.
        idx (int): index.
    
    Returns:
        fig: Figure.
    """
    if not isinstance(gene_index,int):
        try:
            idx = gene_index[gene_chosen]
        except KeyError:
            gene_chosen = gene_chosen.split('_')
            gene_chosen = gene_chosen[-2]
            idx = gene_index[gene_chosen]

        coords = []
        idx = list(cell_adata.var.index).index(gene_chosen)
        for region in drawn_geojson['features']:
            temp = region['geometry']['coordinates'][0]
            temp = [mapper(*point) for point in temp]
            temp = [[point[1], point[0]] for point in temp]  # y, x -> x, y
            coords.append(temp)
        
        target = np.array([])
        for coord in coords:
            roi = Polygon(coord)
            locs = list(map(lambda x: roi.contains(Point(x)), cell_adata.obsm['spatial']))
            to_keep = cell_adata[locs].copy()
            if scipy.sparse.issparse(to_keep.X):
                array = to_keep.X[:, idx].toarray().ravel()
            else:
                array = to_keep.X[:, idx].ravel()
            target = np.concatenate((target, array))
        hist = go.Figure(
            data=go.Histogram(
                x=target,
            ),
            layout=go.Layout(
                title=f'Histogram: selected region of {gene_chosen}<br>                  for cell data',
                xaxis={'title': 'Gene expression','showline': True},
                yaxis={'title': 'Number of cells', 'showline': True},
                font=dict(color='white'),
                paper_bgcolor='black',
                plot_bgcolor='black'
            ),
        )
        table = go.Figure(
            data=go.Table(
                header={
                    'values': ['Mean', 'Median', 'Std'],
                    'align': 'center',
                    'fill_color': 'black'
                },
                cells={
                    'values': [
                        np.round(np.mean(target), 2),
                        np.round(np.median(target), 2),
                        np.round(np.std(target), 2),
                    ],
                    'align': 'center',
                    'fill_color': 'black'
                },
            ),
            layout=go.Layout(
                title=f'Statistics: selected region of {gene_chosen}<br>               for cell data',
                font=dict(color='white'),
                paper_bgcolor='black',
                plot_bgcolor='black'
            ),
        )

    
        return dcc.Graph(figure=hist, style={"height":"300px","color":"black"}), dcc.Graph(figure=table,style={"height":"300px","color":"black"})
    else:
        raise dash.exceptions.PreventUpdate


def show_mouse_position(clickData, folder_id, work_dir):
    """Show mouse position.
    
    Args:
        clickData (dict): Click data.
    
    Returns:
        str: Mouse position.
    """
    thor, args, p_input_json = get_parameter(folder_id, work_dir)
    data_path, cache_path = get_data_path_cache_path(work_dir)
    sample_id = args["sampleId"]
    height = args["heightWidth"][0]
    width = args["heightWidth"][1]
    max_dim = max(height, width)
    
    if clickData is None:
        return html.Div([
            html.Br(),
            html.H5('Click on the map to get coordinates',className="text")
            ])
    else:
        resize_factor = 1
        lat = clickData['latlng']['lat']
        lon = clickData['latlng']['lng']
        y, x = mapper(lon, lat)
        x = np.ceil(x / resize_factor)
        y = np.ceil(y / resize_factor)
        return html.Div([
            html.Br(), 
            html.H5(f'You clicked on x: {x}, y: {y}',className="text")
         ])

def cell_selection_interface(n_clicks, folder_id, work_dir):
    """
    We will the latest selected area to search for similar cell in the cell data.

    The latest selected area will be read from the coords.json file [-1].
    """
    if n_clicks:
        global area
        thor, args, p_input_json = get_parameter(folder_id, work_dir)
        data_path, cache_path = get_data_path_cache_path(work_dir)
        sample_id = args["sampleId"]

        copy_and_rename_file(n_clicks, folder_id, work_dir)

        # Read the coords.json file (always in the unit of the original image pixels)
        coords_path = f'{work_dir}/user{folder_id}/selected_area/coords.json'
        
        # S3-safe load
        coords = vio.load_json(coords_path)[-1]

        # Pengzhi added this part to resize the coords to match the spatial coordiates in the adata
        # 2025-01-31
        height = args["heightWidth"][0]
        width = args["heightWidth"][1]
        max_dim = max(height, width)


        ad_ROI = get_region(cell_adata, coords)
        # Pengzhi end
        
        #ad_ROI = ad.read_h5ad(f'{work_dir}/user{folder_id}/selected_area/roi-0.h5ad')
        adata = cell_adata
        df_sele_mask = cell_selection_main(adata, ad_ROI)
        # reorder cell order
        df_sele_mask = df_sele_mask.reindex(list(cell_adata.obs.index))
        files = files_generate(sample_id)
        # open cell info and put cnv info under label


         # add cell select column to cell info
        cell_info = vio.read_csv(os.path.join(data_path, files["cell-info"]), index_col=0)
        cell_info["cell_select"] = list(df_sele_mask.iloc[:, 0])
        # S3-safe write
        vio.write_csv(cell_info, os.path.join(data_path, files["cell-info"]))


        # add cell select column to cell barcode to download
        cell_info = vio.read_csv(os.path.join(data_path, files["cell-barcode"]), index_col=0,header=None)
        cell_info["cell_select"] = list(df_sele_mask.iloc[:, 0])
        vio.write_csv(cell_info, f'{work_dir}/user{folder_id}/similar_cell_info.csv')


        if f"area{area}" in p_input_json["Area"]:
            area = area + 1
        else:
            area = area
        sample_id_area = sample_id + "-"+ f"area{area}"
        p_input_json["Area"].append(f"area{area}")
        args["sampleIdArea"] = sample_id_area
        dumpjson_parameter_from_user_input(folder_id, work_dir, args, p_input_json)
        calculation_similar_cell(folder_id, work_dir)

        # copy similar_cell_info.csv to folder user{folder_id}/selected_area and zip it
        dir_path = f'{work_dir}/user{folder_id}/selected_area/'
        vio.copy(f'{work_dir}/user{folder_id}/similar_cell_info.csv', dir_path)
        vio.zip_folder_s3(dir_path, f"{work_dir}/user{folder_id}/searched_cells.zip")

        return dcc.send_file(f"{work_dir}/user{folder_id}/searched_cells.zip")
    



def clear_cache_forcall(*args):
    if len(args) >= 4:
        n_clicks, folder_id, work_dir = args[:3]
        if not n_clicks:
            return None
    elif len(args) >= 2:
        folder_id, work_dir = args[:2]
    else:
        raise TypeError("clear_cache_forcall requires folder_id and work_dir")

    # 1. Clean up "work_dir" (local cache or db/data) via vio
    if work_dir and vio.exists(work_dir):
        try:
            vio.rmdir(work_dir)
            print(f"✅ Deleted cache: {work_dir}")
        except Exception as e:
            print(f"⚠️ Failed to delete {work_dir}: {e}")
    
    # (S3 / EC2 cleanup removed — local-only mode)

    print("Stopping server...")
    os._exit(0)
    return None
