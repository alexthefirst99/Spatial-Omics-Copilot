import argparse
import os

# GDAL optimizations for local TIFF access
os.environ["GDAL_DISABLE_READDIR_ON_OPEN"] = "EMPTY_DIR"
os.environ["VSI_CACHE"] = "TRUE"
os.environ["VSI_CACHE_SIZE"] = "536870912"  # 512MB
os.environ["CPL_VSIL_CURL_ALLOWED_EXTENSIONS"] = ".tiff,.tif,.png,.jpg,.json"
os.environ["GDAL_HTTP_MERGE_CONSECUTIVE_RANGES"] = "YES"
os.environ['GDAL_ALLOW_LARGE_LIBJPEG_MEM_ALLOC'] = 'YES'

import sys
_APP_DIR_FOR_PATHS = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT_FOR_PATHS = os.path.abspath(os.path.join(_APP_DIR_FOR_PATHS, '..'))
_SRC_DIR_FOR_PATHS = os.path.join(_PROJECT_ROOT_FOR_PATHS, 'src')
for _path in (_PROJECT_ROOT_FOR_PATHS, _SRC_DIR_FOR_PATHS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import uuid
import time
import threading
import webbrowser
import shutil
import fcntl
import concurrent.futures
import copy
import ollama
import tifffile
import numpy as np
from PIL import Image as _PILImage
_PILImage.MAX_IMAGE_PIXELS = None
import dash
import plotly.graph_objs as go
from dash import html, dcc
from flask import request, jsonify, stream_with_context, Response
import tempfile
from flask_cors import CORS
from dash.dependencies import Input, Output, State
from flask import request, abort
import json
from dash import Input, Output, State, ALL, ctx
from flask import request, jsonify, send_file, redirect
import urllib.parse
import urllib.request
import urllib.error
import base64
import mimetypes
import ssl

# ----------------------------------------------------------------------------
# LOCAL CONFIG
# ----------------------------------------------------------------------------
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_APP_DIR, '..'))
_DATA_DIR = os.path.join(_PROJECT_ROOT, 'data')
_LOCAL_DASH_VIV_VIEWER = os.path.join(_PROJECT_ROOT, 'packages', 'dash_viv_viewer')
if _LOCAL_DASH_VIV_VIEWER not in sys.path:
    sys.path.insert(0, _LOCAL_DASH_VIV_VIEWER)

from app.config import get_bool, get_config, get_path

# Directory for chat session JSON files
CHAT_DIR = get_path('paths.chat_dir', os.path.join(_DATA_DIR, 'chat_sessions'), env='COPILOT_CHAT_DIR')
os.makedirs(CHAT_DIR, exist_ok=True)

# Tutorial image path is configured in config/app.yaml.
TUTORIAL_IMAGE_PATH = get_path(
    'paths.tutorial_image',
    os.path.join(_PROJECT_ROOT, 'tutorial', 'loki_tutorial_hskin_melanoma_downsampled.ome.tif'),
    env='COPILOT_TUTORIAL_IMAGE',
)
TUTORIAL_SAMPLE_ID = "copilot-tutorial"
TUTORIAL_SAMPLE_ID_FILE = "copilot-tutorial-file-name"

# Base directory for temp/cache files (use /tmp or a configurable path)
TMP_BASE = get_path('paths.tmp_base', os.path.join(_PROJECT_ROOT, 'tmp_data'), env='COPILOT_TMP_BASE')
os.makedirs(TMP_BASE, exist_ok=True)

# Import custom layout and utilities
try:
    from app.layout import create_layout
    from app.utils import setup_work_dir
    import niceview.utils.io as vio
    import app.status_store as status_store
except ImportError:
    from layout import create_layout
    from utils import setup_work_dir
    import niceview.utils.io as vio

# Import sub-modules
try:
    from app.inference import DEFAULT_OLLAMA_MODEL, run_model_inference
    from app.session import (
        CHAT_DIR as _CHAT_DIR_mod,
        _session_path,
        _lock_and_read_session, _lock_and_write_session,
        _read_session, _write_session,
        safe_update_session, safe_update_streaming_message,
        safe_update_last_assistant_image,
    )
    from app.image_utils import (
        OME_CACHE_LOCKS, OME_CACHE_LOCKS_GUARD,
        crop_image_by_roi,
        get_ome_cache_path, ensure_ome_tiff_cached,
    )
    from app.worker import (
        _bot_executor, processing_keys, processing_lock,
        process_session, enqueue_chat_job,
    )
    from app.routes import register_chat_routes
except ImportError:
    from inference import DEFAULT_OLLAMA_MODEL, run_model_inference
    from session import (
        CHAT_DIR as _CHAT_DIR_mod,
        _session_path,
        _lock_and_read_session, _lock_and_write_session,
        _read_session, _write_session,
        safe_update_session, safe_update_streaming_message,
        safe_update_last_assistant_image,
    )
    from image_utils import (
        OME_CACHE_LOCKS, OME_CACHE_LOCKS_GUARD,
        crop_image_by_roi,
        get_ome_cache_path, ensure_ome_tiff_cached,
    )
    from worker import (
        _bot_executor, processing_keys, processing_lock,
        process_session, enqueue_chat_job,
    )
    from routes import register_chat_routes

# Import tile server
# localtileserver removed - VivViewer serves OME-TIFFs directly

def add_workspace_to_map(workspace, port):
    os.makedirs(_DATA_DIR, exist_ok=True)
    map_path = get_path('paths.workspace_map', os.path.join(_DATA_DIR, "workspace_map.json"), env='COPILOT_WORKSPACE_MAP')
    try:
        with open(map_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data[workspace] = port
    tmp_path = map_path + '.tmp'
    with open(tmp_path, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, map_path)

gene_chosen = None
# Import custom interface logic
from niceview.interface.callback import (
    upload_image, upload_spatial_h5ad, reset, save_roi, clear_cache_forcall
)
from rag.deg import get_roi_high_expression_genes, get_cluster_high_expression_genes

from niceview.interface.interface import (
    prepare_file_folder, update_data_cache,
    dump_default_para_arg, add_workspace_mapping
)

# Parse arguments
HOST = '0.0.0.0'
workdir = setup_work_dir()
parser = argparse.ArgumentParser(description='Run Dash app.')
parser.add_argument('--port', type=int, default=8080, help='Port to run the app on')
parser.add_argument('--wd', type=str, default=workdir, help='Working directory for the app')
parser.add_argument("--workspace", type=str, default=None, help='Workspace slug used in the app URL')
parser.add_argument("--token", type=str, default=None, help='Deprecated alias for --workspace')
parser.add_argument("--hot-reload", action="store_true", help="Enable Dash hot reload while developing")
args = parser.parse_args()
args.workspace = args.workspace or args.token or "demo"
args.hot_reload = args.hot_reload or get_bool("app.hot_reload", default=False, env="COPILOT_HOT_RELOAD")
add_workspace_to_map(args.workspace, args.port)


# ----------------------------------------------------------------------------
# DASH APP
# ----------------------------------------------------------------------------


def main():
    # Setup paths and IDs
    WORKSPACE_ID = args.workspace
    APP_BASE_PATH = f"/workspaces/{WORKSPACE_ID}"
    work_dir = args.wd
    folder_id = ""
    app_dir = os.path.dirname(os.path.realpath(__file__))

    # Initialize Flask Server explicitly
    from flask import Flask
    server = Flask(__name__)
    CORS(server) # Enable CORS for all routes (Fixes Tile Loading Status 0)

    @server.route(f"/app/{WORKSPACE_ID}")
    @server.route(f"/app/{WORKSPACE_ID}/")
    def redirect_legacy_app_root():
        return redirect(f"{APP_BASE_PATH}/", code=308)

    @server.route(f"/app/{WORKSPACE_ID}/<path:subpath>")
    def redirect_legacy_app_path(subpath):
        return redirect(f"{APP_BASE_PATH}/{subpath}", code=308)

    # Register Chat Routes BEFORE Dash.
    register_chat_routes(server, WORKSPACE_ID, work_dir, base_path=APP_BASE_PATH)

    # Initialize Dash app
    app = dash.Dash(
        __name__,
        server=server,
        meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
        requests_pathname_prefix=f"{APP_BASE_PATH}/",
        routes_pathname_prefix=f"{APP_BASE_PATH}/",
        suppress_callback_exceptions=True
    )

    # (localtileserver removed - VivViewer serves OME-TIFFs via /ome_tiff proxy)

    # Configure app
    temp_dir = f"{work_dir}/data_input_temp/tmp/"
    app.title = "Spatial Omics Copilot"
    prepare_file_folder(folder_id, work_dir)
    update_data_cache(folder_id, work_dir)
    dump_default_para_arg(folder_id, work_dir)

    # Save workspace mapping. Keep the legacy keys because niceview reads them.
    user_token_info = {
        "user-port": args.port,
        "user-token": WORKSPACE_ID,
        "workspace": WORKSPACE_ID,
        "tile-port": int(args.port)+1,
    }
    add_workspace_mapping(work_dir, folder_id, user_token_info)

    app.layout = create_layout(work_dir, folder_id)

    # ----------------------- Local Chunked Upload -----------------------
    @app.server.route(f"{APP_BASE_PATH}/upload_chunk", methods=['POST'])
    def upload_chunk():
        try:
            filename = request.headers.get('x-filename')
            if not filename:
                return jsonify({"error": "Missing filename"}), 400

            upload_id = request.headers.get('x-upload-id')
            if upload_id:
                save_dir = os.path.join(work_dir, "data_input_temp", "tmp", upload_id)
            else:
                save_dir = os.path.join(work_dir, "data_input_temp", "tmp")
            os.makedirs(save_dir, exist_ok=True)

            filepath = os.path.join(save_dir, filename)

            action = request.headers.get('x-action', 'append')
            mode = 'ab' if action == 'append' else 'wb'

            with open(filepath, mode) as f:
                shutil.copyfileobj(request.stream, f)

            if upload_id:
                status_store.update_status(upload_id, 1, "Upload received. Starting processing...")

            return jsonify({"status": "success", "local_path": filepath}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500


    @app.server.route(f"{APP_BASE_PATH}/upload_status/<job_id>", methods=["GET"])
    def get_upload_status(job_id):
        return jsonify(status_store.get_status(job_id))

    # ----------------------- Upload Callbacks -----------------------

    # Callback to handle upload of H&E image
    # Callback to handle upload of H&E image
    @app.callback(
        Output('h5ad-upload-summary', 'children'),
        Input('upload-spatial-h5ad-result', 'value')
    )
    def callback_upload_spatial_h5ad(filenames_upload_h5ad):
        if not filenames_upload_h5ad:
            return dash.no_update
        return upload_spatial_h5ad([filenames_upload_h5ad], folder_id, work_dir)

    # ----------------------- Standard Dash Callback (Custom Uploader) -----------------------
    # Replaced @du.callback because we are using custom JS/HTML uploader now
    @app.callback(
        [Output('status1', 'children'),
         Output('processing-job-id', 'data')],
        [Input('upload-data-image-result-dash', 'value')]
    )
    def callback_on_completion(upload_path):
        print(f"DEBUG: callback_on_completion ENTRY: path={upload_path}", flush=True)

        if not upload_path:
            print("DEBUG: upload_path is empty, ignoring.", flush=True)
            return dash.no_update, dash.no_update

        if upload_path == TUTORIAL_IMAGE_PATH:
            print("DEBUG: Tutorial image selected; using prebuilt OME-TIFF without preprocessing.", flush=True)
            try:
                args_path = f'{work_dir}/user{folder_id}/args.json'
                args_json = vio.load_json(args_path)
                args_json["sampleId"] = TUTORIAL_SAMPLE_ID
                args_json["fileName"] = "tutorial"
                args_json["sampleIdFile"] = TUTORIAL_SAMPLE_ID_FILE
                args_json["heightWidth"] = [3431, 7117]
                args_json["tutorialImagePath"] = TUTORIAL_IMAGE_PATH
                vio.dump_json(args_json, args_path)
                status_store.update_status("tutorial", 100, "Tutorial image ready")
                return "Tutorial image ready. Click Re-visualize Image.", "tutorial"
            except Exception as e:
                print(f"ERROR preparing tutorial image: {e}", flush=True)
                status_store.update_status("tutorial", 0, f"Error: {str(e)}")
                return f"Tutorial image error: {e}", "tutorial"

        filenames = [upload_path]
        print(f"DEBUG: callback_on_completion PROCESSING {upload_path}", flush=True)

        # Extract Job ID from the local path: .../data_input_temp/tmp/<upload_id>/<filename>
        try:
            parts = upload_path.replace('\\', '/').split('/')
            if 'tmp' in parts:
                idx = parts.index('tmp')
                if idx + 1 < len(parts):
                    job_id = parts[idx + 1]
                else:
                    job_id = str(uuid.uuid4())
            else:
                job_id = str(uuid.uuid4())
        except Exception:
            job_id = str(uuid.uuid4())

        print(f"DEBUG: Extracted Job ID: {job_id}")

        # Initialize status immediately to avoid race condition and missing file
        status_store.update_status(job_id, 0, "Initializing upload...")

        # Start processing in a background thread
        # We pass job_id so the backend can update status_store

        def run_processing_safe():
            try:
                print(f"DEBUG: Processing Thread Started for {job_id}", flush=True)
                upload_image(filenames, folder_id, work_dir, app_dir, job_id)
                try:
                    args_path = f'{work_dir}/user{folder_id}/args.json'
                    args_json = vio.load_json(args_path)
                    if args_json.pop("tutorialImagePath", None):
                        print("DEBUG: Cleared tutorial image marker after user upload.", flush=True)
                        vio.dump_json(args_json, args_path)
                    sample_id_file = args_json.get("sampleIdFile")
                    if sample_id_file:
                        viewer_s3_path = f"{work_dir}/db/cache/{sample_id_file}-gis-wsi-img.tiff"
                        print(f"[viv_debug] Upload processing complete; viewer should use S3 OME-TIFF: {viewer_s3_path}", flush=True)
                except Exception as e:
                    print(f"[viv_debug] Could not resolve viewer S3 OME-TIFF path after upload: {e}", flush=True)
                print(f"DEBUG: Processing Thread Finished for {job_id}", flush=True)
            except Exception as e:
                import traceback
                print(f"ERROR inside processing thread for {job_id}:", flush=True)
                traceback.print_exc()
                status_store.update_status(job_id, 0, f"Error: {str(e)}")

        t = threading.Thread(target=run_processing_safe)
        t.start()

        return "File uploaded. Starting processing...", job_id

    # ----------------------- Visualization + Tools -----------------------

    # Reset visualization and refresh rendering

    @app.callback(
        Output('input-image', 'children', allow_duplicate=True),
        Input("visual-input", "n_clicks"),
        prevent_initial_call='initial_duplicate'
    )
    def callback_reset(n_clicks):
        return reset(n_clicks, None, None, folder_id, work_dir)

    # ----------------------- Save & Export -----------------------

    # Save ROI from drawing tool and show ROI marker genes
    @app.callback(
        Output('status5', 'children'),
        Output('roi-gene-popup', 'children'),
        Input('map-output', 'rois'),
        prevent_initial_call=True
    )
    def callback_save_roi(drawn_geojson):
        status = save_roi(drawn_geojson, folder_id, work_dir)
        if not drawn_geojson:
            return status, []

        try:
            coords_path = f'{work_dir}/user{folder_id}/coords.json'
            if not vio.exists(coords_path):
                return status, []
            coords = vio.load_json(coords_path)

            # Crop the ROI out of the whole-slide image now, once, instead of
            # on every chat message. This callback only fires when the ROI
            # actually changes, so the crop below is always fresh; routes.py
            # and worker.py reuse this file instead of re-cropping per message.
            crop_output_path = f'{work_dir}/user{folder_id}/roi_crop.png'
            crop_meta_path = f'{work_dir}/user{folder_id}/roi_crop_meta.json'
            try:
                args_path = f'{work_dir}/user{folder_id}/args.json'
                sample_id = vio.load_json(args_path).get('sampleId') if vio.exists(args_path) else None
                image_path = f'{work_dir}/db/data/{sample_id}-wsi-img.tiff' if sample_id else None
                if image_path and vio.exists(image_path) and crop_image_by_roi(image_path, coords_path, crop_output_path):
                    vio.dump_json({"image_path": image_path}, crop_meta_path)
                else:
                    print(f"DEBUG: ROI crop-at-selection produced no crop (sample_id={sample_id})")
                    if vio.exists(crop_meta_path):
                        vio.remove(crop_meta_path)
            except Exception as e_crop:
                print(f"DEBUG: ROI crop-at-selection failed: {e_crop}")
                if vio.exists(crop_meta_path):
                    vio.remove(crop_meta_path)

            deg_result = get_roi_high_expression_genes(
                work_dir, coords, folder_id=folder_id, top_n=25
            )
            gene_objects = deg_result["top_genes"] if deg_result and deg_result.get("top_genes") else []

            vio.dump_json({"gene_objects": gene_objects}, f'{work_dir}/user{folder_id}/roi_context.json')

            if not gene_objects:
                return status, html.Div(className="roi-gene-card", children=[
                    html.Div("ROI marker genes", className="roi-gene-title"),
                    html.Div("Upload a spatial .h5ad file to see enriched genes.", className="roi-gene-empty"),
                ])

            gene_rows = [
                html.Div(className="roi-gene-row", children=[
                    html.Span(g["gene"], className="roi-gene-name"),
                    html.Span(f"log2FC {g['log2_fold_change']:.2g}", className="roi-gene-score"),
                ]) for g in gene_objects[:25]
            ]
            n_spots = deg_result.get("selected_spots", 0)
            return status, html.Div(className="roi-gene-card", children=[
                html.Div(className="roi-gene-header", children=[
                    html.Div("ROI marker genes", className="roi-gene-title"),
                    html.Div(f"{n_spots} spots", className="roi-gene-count"),
                ]),
                html.Div(gene_rows, className="roi-gene-list"),
            ])
        except Exception as e:
            print(f"ROI gene popup error: {e}")
            return status, html.Div(className="roi-gene-card", children=[
                html.Div("ROI marker genes", className="roi-gene-title"),
                html.Div("Could not calculate ROI genes.", className="roi-gene-empty"),
            ])

    # Show marker genes when a spatial cluster legend row is clicked.
    @app.callback(
        Output('roi-gene-popup', 'children', allow_duplicate=True),
        Output('map-output', 'selected_cluster', allow_duplicate=True),
        Input({"type": "cluster-legend-btn", "index": ALL}, "n_clicks"),
        prevent_initial_call=True
    )
    def callback_cluster_gene_popup(n_clicks_list):
        if not ctx.triggered_id or not any(n_clicks_list or []):
            return dash.no_update, dash.no_update

        cluster_id = str(ctx.triggered_id["index"])

        try:
            deg_result = get_cluster_high_expression_genes(
                work_dir, cluster_id, folder_id=folder_id, top_n=25
            )
            gene_objects = deg_result["top_genes"] if deg_result and deg_result.get("top_genes") else []

            vio.dump_json({
                "cluster_id": cluster_id,
                "gene_objects": gene_objects,
            }, f'{work_dir}/user{folder_id}/cluster_context.json')

            if not gene_objects:
                return html.Div(className="roi-gene-card", children=[
                    html.Div(f"Cluster {cluster_id} marker genes", className="roi-gene-title"),
                    html.Div("Upload a spatial .h5ad file and re-visualize to see cluster genes.", className="roi-gene-empty"),
                ]), cluster_id

            gene_rows = [
                html.Div(className="roi-gene-row", children=[
                    html.Span(g["gene"], className="roi-gene-name"),
                    html.Span(f"log2FC {g['log2_fold_change']:.2g}", className="roi-gene-score"),
                ]) for g in gene_objects[:25]
            ]
            n_spots = deg_result.get("selected_spots", 0)
            return html.Div(className="roi-gene-card", children=[
                html.Div(className="roi-gene-header", children=[
                    html.Div(f"Cluster {cluster_id} marker genes", className="roi-gene-title"),
                    html.Div(f"{n_spots} spots", className="roi-gene-count"),
                ]),
                html.Div(gene_rows, className="roi-gene-list"),
            ]), cluster_id
        except Exception as e:
            print(f"Cluster gene popup error: {e}")
            return html.Div(className="roi-gene-card", children=[
                html.Div(f"Cluster {cluster_id} marker genes", className="roi-gene-title"),
                html.Div("Could not calculate cluster genes.", className="roi-gene-empty"),
            ]), cluster_id


    timer = threading.Timer(7200, clear_cache_forcall, args=(WORKSPACE_ID, work_dir))
    timer.daemon = True
    timer.start()

    # Clear temp data and exit app
    @app.callback(
        Output('status6', 'children'),
        Input('clear-cache', 'n_clicks'),
        prevent_initial_call=True
    )
    def callback_clear_cache_forcall(n_clicks):
        print(f"DEBUG: Clear Cache Clicked. n_clicks={n_clicks}")

        if n_clicks > 0:
            print("DEBUG: Executing clear_cache_forcall...")
            # We don't check for existence anymore, we just force exit
            clear_cache_forcall(WORKSPACE_ID, work_dir)
            return None
        return dash.no_update

    # --- OLLAMA WARMUP ---
    # Ollama only loads a model into memory on first use, which costs several
    # seconds (measured ~7.5s for the 7b vision model vs ~0.4s once warm) and
    # unloads it again after ollama.keep_alive of inactivity. Warming up here
    # means the user's first real chat message never pays that cold-start
    # cost. Both models are warmed since the UI lets the user switch to
    # vision mid-session.
    def warmup_ollama():
        text_model = get_config('ollama.model', DEFAULT_OLLAMA_MODEL, env='OLLAMA_MODEL')
        vision_model = get_config('ollama.vision_model', text_model, env='OLLAMA_VISION_MODEL')
        for model_name in dict.fromkeys([text_model, vision_model]):  # de-duped, order preserved
            print(f"DEBUG: Sending warmup 'hi' to Ollama ({model_name})...")
            try:
                enqueue_chat_job(
                    session_id=f"{WORKSPACE_ID}__warmup__{model_name}",
                    model=f"ollama:{model_name}",
                    prompt="hi",
                    images=[],
                    work_dir=work_dir,
                    roi_path=None,
                    visible=False # Invisible to user
                )
                print(f"DEBUG: Warmup 'hi' sent successfully ({model_name}).")
            except Exception as e:
                print(f"DEBUG: Warmup failed ({model_name}): {e}")

    if get_bool("ollama.warmup", default=False, env="OLLAMA_WARMUP"):
        threading.Thread(target=warmup_ollama, daemon=True).start()

    # Open browser after a short delay to let the server start
    url = f"http://localhost:{args.port}{APP_BASE_PATH}"
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    # Launch browser and run app
    app.run_server(host=HOST, port=args.port, debug=False, dev_tools_hot_reload=args.hot_reload)




if __name__ == "__main__":
    main()
