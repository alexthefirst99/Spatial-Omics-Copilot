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
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import uuid
import time
import threading
import webbrowser
import shutil
import fcntl
import concurrent.futures
import subprocess
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

# Directory for chat session JSON files
CHAT_DIR = os.environ.get('LOKI_CHAT_DIR', os.path.join(_PROJECT_ROOT, 'chat_sessions'))
os.makedirs(CHAT_DIR, exist_ok=True)

# Tutorial image — set LOKI_TUTORIAL_IMAGE env var to point to a local file
TUTORIAL_IMAGE_PATH = os.environ.get(
    'LOKI_TUTORIAL_IMAGE',
    os.path.join(_PROJECT_ROOT, 'tutorial', 'loki_tutorial_hskin_melanoma_downsampled.ome.tif')
)
TUTORIAL_SAMPLE_ID = "loki-tutorial"
TUTORIAL_SAMPLE_ID_FILE = "loki-tutorial-file-name"

# Base directory for temp/cache files (use /tmp or a configurable path)
TMP_BASE = os.environ.get('LOKI_TMP_BASE', os.path.join(_PROJECT_ROOT, 'tmp_data'))
os.makedirs(TMP_BASE, exist_ok=True)

OME_CACHE_LOCKS = {}
OME_CACHE_LOCKS_GUARD = threading.Lock()

# Import custom layout and utilities
try:
    from app.layout import create_layout
    from app.utils import setup_work_dir, open_browser
    import niceview.utils.io as vio
    import app.status_store as status_store
except ImportError:
    from layout import create_layout
    from utils import setup_work_dir, open_browser
    import niceview.utils.io as vio

# Import tile server
# localtileserver removed - VivViewer serves OME-TIFFs directly

def add_token_to_map(token, port):
    map_path = os.path.join(_PROJECT_ROOT, "proxy_map.json")
    try:
        with open(map_path) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data[token] = port
    tmp_path = map_path + '.tmp'
    with open(tmp_path, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, map_path)

def get_ome_cache_path(path, token):
    import hashlib
    path_hash = hashlib.md5(path.encode()).hexdigest()[:12]
    basename = os.path.splitext(os.path.basename(path))[0]
    parent_cache_dir = os.path.join(TMP_BASE, "ome_tiff_cache")
    ome_cache_dir = os.path.join(parent_cache_dir, token)
    return parent_cache_dir, os.path.join(ome_cache_dir, f"{path_hash}_{basename}.ome.tiff")

def ensure_ome_tiff_cached(path, token):
    from dash_viv_viewer.utils import convert_to_ome_tiff

    parent_cache_dir, ome_local_path = get_ome_cache_path(path, token)
    os.makedirs(os.path.dirname(ome_local_path), exist_ok=True)

    with OME_CACHE_LOCKS_GUARD:
        lock = OME_CACHE_LOCKS.setdefault(ome_local_path, threading.Lock())

    with lock:
        if os.path.exists(ome_local_path):
            print(f"[ome_tiff] Serving cached OME-TIFF: {ome_local_path}")
            return ome_local_path

        if os.path.exists(path):
            print(f"[ome_tiff] Converting local file to OME-TIFF: {ome_local_path}")
            convert_to_ome_tiff(path, ome_local_path)
        else:
            raise FileNotFoundError(path)

    return ome_local_path

gene_chosen = None
# Import custom interface logic
from niceview.interface.callback import (
    upload_image, upload_spot_data, upload_spatial_h5ad, build_roi_gene_context, get_roi_high_expression_genes, upload_cell_data,
    show_cell_spot_upload, update_output_visual,
    upload_pathway, upload_coordinate, get_pathway_output,
    upload_cnv, get_gene, cell_vmin_vmax, spot_vmin_vmax,
    reset, copy_and_rename_file, save_roi,
    cell_selection_interface,
    clear_cache_forcall
)

from niceview.interface.interface import (
    prepare_file_folder, update_data_cache,
    dump_default_para_arg, add_token_mapping, calculation_cell_detection
)

# Parse arguments
HOST = '0.0.0.0'
workdir = setup_work_dir()
parser = argparse.ArgumentParser(description='Run Dash app.')
parser.add_argument('--port', type=int, default=8080, help='Port to run the app on')
parser.add_argument('--wd', type=str, default=workdir, help='Working directory for the app')
parser.add_argument("--token", type=str, required=True, help='Token to access')
args = parser.parse_args()
add_token_to_map(args.token,args.port)




def _session_path(session_id):
    session_dir = os.path.join(CHAT_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    return os.path.join(session_dir, "session.json")

def _dirty_flag_path():
    return os.path.join(CHAT_DIR, "dirty_flag")

def _lock_and_read_session(path):
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        try:
            return json.load(f)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)

def _lock_and_write_session(path, data):
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            json.dump(data, f, indent=2)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    os.replace(tmp_path, path)

def _touch_dirty_flag():
    path = _dirty_flag_path()
    try:
        with open(path, 'w') as f:
            f.write("1")
    except Exception as e:
        print(f"Warning: Failed to update dirty flag: {e}")


# ----------------------------------------------------------------------------
# BOT WORKER — runs inference and Loki directly in background threads
# ----------------------------------------------------------------------------

def _read_session(session_id):
    return _lock_and_read_session(_session_path(session_id))

def _write_session(session_id, data):
    _lock_and_write_session(_session_path(session_id), data)
    return True

DATA_CACHE_DIR = os.path.join(_PROJECT_ROOT, "hpc_data")
os.makedirs(DATA_CACHE_DIR, exist_ok=True)

_bot_executor = concurrent.futures.ThreadPoolExecutor(max_workers=50)
processing_keys = set()
processing_lock = threading.Lock()


def update_progress(session_id, percent, message):
    try:
        status_data = {
            "percent": percent,
            "message": message,
            "timestamp": time.time(),
            "status": "processing" if percent < 100 else "done"
        }
        status_dir = os.path.join(CHAT_DIR, session_id)
        os.makedirs(status_dir, exist_ok=True)
        path = os.path.join(status_dir, "loki_status.json")
        tmp = path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(status_data, f)
        os.replace(tmp, path)
    except Exception as e:
        print(f"Failed to update progress: {e}")


def copy_local_file(src_path, dst_path):
    try:
        if not os.path.exists(src_path):
            print(f"[copy] Source not found: {src_path}")
            return False
        shutil.copy2(src_path, dst_path)
        return True
    except Exception as e:
        print(f"[copy] Error copying {src_path} -> {dst_path}: {e}")
        return False


def crop_image_by_roi(image_path, roi_path, output_path):
    try:
        with open(roi_path, 'r') as f:
            roi_data = json.load(f)

        all_points = []
        if 'features' in roi_data:
            for feature in roi_data['features']:
                geom = feature.get('geometry', {})
                coords = geom.get('coordinates', [])
                if geom.get('type') == 'Polygon':
                    for ring in coords:
                        all_points.extend(ring)
                elif geom.get('type') == 'MultiPolygon':
                    for poly in coords:
                        for ring in poly:
                            all_points.extend(ring)
        elif 'geometry' in roi_data:
            geom = roi_data['geometry']
            coords = geom.get('coordinates', [])
            if geom.get('type') == 'Polygon':
                for ring in coords:
                    all_points.extend(ring)
        if not all_points and isinstance(roi_data, list):
            for poly in roi_data:
                all_points.extend(poly)

        if not all_points:
            print("No coordinates found in ROI file.")
            return False

        pts = np.array(all_points)
        x_min, y_min = np.min(pts, axis=0)
        x_max, y_max = np.max(pts, axis=0)
        x_min = max(0, int(x_min))
        y_min = max(0, int(y_min))
        x_max = int(x_max)
        y_max = int(y_max)

        crop = None
        try:
            import rasterio
            from rasterio.windows import Window
            with rasterio.open(image_path) as src:
                x_min = max(0, min(x_min, src.width))
                y_min = max(0, min(y_min, src.height))
                x_max = max(0, min(x_max, src.width))
                y_max = max(0, min(y_max, src.height))
                if x_max > x_min and y_max > y_min:
                    window = Window(x_min, y_min, x_max - x_min, y_max - y_min)
                    crop = src.read(window=window)
                    crop = np.moveaxis(crop, 0, -1)
                    if crop.shape[2] == 1:
                        crop = crop[:, :, 0]
        except Exception as e_rio:
            print(f"Rasterio crop failed ({e_rio}), trying tifffile.")
            try:
                with tifffile.TiffFile(image_path) as tif:
                    page = tif.pages[0]
                    ih, iw = page.shape[0], page.shape[1]
                    x_max = min(x_max, iw)
                    y_max = min(y_max, ih)
                    if x_max > x_min and y_max > y_min:
                        crop = page.asarray()[y_min:y_max, x_min:x_max]
            except Exception as e_tif:
                print(f"tifffile failed: {e_tif}. Trying PIL.")
                try:
                    img = _PILImage.open(image_path)
                    iw, ih = img.size
                    x_max = min(x_max, iw)
                    y_max = min(y_max, ih)
                    if x_max > x_min and y_max > y_min:
                        crop = np.array(img.crop((x_min, y_min, x_max, y_max)))
                except Exception as e_pil:
                    print(f"PIL failed: {e_pil}")

        if crop is not None and crop.size > 0:
            if len(crop.shape) == 3 and crop.shape[2] == 4:
                if output_path.lower().endswith(('.jpg', '.jpeg')):
                    crop = crop[:, :, :3]
            _PILImage.fromarray(crop).save(output_path)
            return True
        else:
            print("Crop was empty or failed.")
            return False
    except Exception as e:
        print(f"Error cropping image: {e}")
        return False


def _image_to_data_url(image_path):
    mime_type = mimetypes.guess_type(image_path)[0] or "image/png"
    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _openai_messages_from_history(messages):
    openai_messages = []
    for msg in messages:
        role = msg.get("role")
        if role not in ("system", "user", "assistant"):
            continue

        content = msg.get("content", "")
        valid_images = [img for img in msg.get("images", []) if os.path.exists(img)]
        if valid_images and role == "user":
            parts = [{"type": "text", "text": content or ""}]
            for img in valid_images:
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": _image_to_data_url(img)}
                })
            openai_messages.append({"role": role, "content": parts})
        else:
            openai_messages.append({"role": role, "content": content or ""})
    return openai_messages


def _stream_openai_chat(messages, model_name):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        yield "OpenAI API key is not set. Please set OPENAI_API_KEY before using ChatGPT API."
        return

    payload = {
        "model": model_name,
        "messages": _openai_messages_from_history(messages),
        "stream": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    def stream_with_context(ssl_context):
        with urllib.request.urlopen(req, timeout=120, context=ssl_context) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data: "):
                    continue
                event = line[6:]
                if event == "[DONE]":
                    break
                try:
                    chunk = json.loads(event)
                    delta = chunk["choices"][0].get("delta", {})
                    text = delta.get("content")
                    if text:
                        yield text
                except Exception as e:
                    print(f"OpenAI stream parse error: {e}")

    try:
        ssl_context = None
        if os.environ.get("OPENAI_INSECURE_SSL") == "1":
            print("WARNING: OPENAI_INSECURE_SSL=1, SSL certificate verification is disabled for OpenAI requests.")
            ssl_context = ssl._create_unverified_context()
        else:
            try:
                import certifi
                ssl_context = ssl.create_default_context(cafile=certifi.where())
            except Exception as cert_error:
                print(f"OpenAI SSL context fallback: {cert_error}")

        try:
            yield from stream_with_context(ssl_context)
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" not in str(e) and "CERTIFICATEVERIFYFAILED" not in str(e):
                raise
            print("WARNING: OpenAI SSL verification failed; retrying once without certificate verification for local dev.")
            yield from stream_with_context(ssl._create_unverified_context())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"OpenAI HTTP Error: {e.code} {body}")
        yield f"OpenAI API error {e.code}: {body}"
    except Exception as e:
        print(f"OpenAI Error: {e}")
        yield f"Error querying OpenAI: {e}"


def run_model_inference(messages, provider=None, model_name=None):
    provider = (provider or "ollama").lower()

    if messages and "run loki analysis" in messages[-1].get("content", "").lower():
        return

    if provider == "openai":
        selected_model = model_name or os.environ.get("OPENAI_MODEL", "gpt-4o")
        print(f"DEBUG: Calling OpenAI (model={selected_model}, history={len(messages)})")
        yield from _stream_openai_chat(messages, selected_model)
        return

    selected_model = model_name or os.environ.get("OLLAMA_MODEL", "qwen3-vl:30b")
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11435")
    os.environ["OLLAMA_HOST"] = host

    clean_history = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        clean_msg = {"role": role, "content": content}
        valid_images = [img for img in msg.get("images", []) if os.path.exists(img)]
        if valid_images:
            clean_msg["images"] = valid_images
        clean_history.append(clean_msg)

    try:
        print(f"DEBUG: Calling Ollama (model={selected_model}, history={len(clean_history)})")
        client = ollama.Client(host=host)
        stream = client.chat(model=selected_model, messages=clean_history, stream=True)
        chunk_count = 0
        for chunk in stream:
            chunk_count += 1
            yield chunk["message"]["content"]
        print(f"DEBUG: Ollama stream finished. Chunks={chunk_count}")
    except Exception as e:
        print(f"Ollama Error: {e}")
        yield f"(Offline mode) Error querying model: {e}"


def safe_update_session(session_id, new_message):
    retries = 10
    while retries > 0:
        try:
            data = _read_session(session_id) or {"session_id": session_id, "messages": []}
            data["messages"].append(new_message)
            data["updated_at"] = time.time()
            if _write_session(session_id, data):
                return True
        except Exception as e:
            print(f"Async update failed: {e}")
        retries -= 1
        time.sleep(0.2 * (11 - retries))
    return False


def safe_update_streaming_message(session_id, content, streaming=True):
    retries = 5
    while retries > 0:
        try:
            data = _read_session(session_id)
            if not data:
                return False
            msgs = data.get("messages", [])
            if msgs and msgs[-1]["role"] == "assistant":
                msgs[-1]["content"] = content
                if streaming:
                    msgs[-1]["streaming"] = True
                else:
                    msgs[-1].pop("streaming", None)
                data["updated_at"] = time.time()
                if _write_session(session_id, data):
                    return True
            return False
        except Exception as e:
            print(f"Streaming update failed: {e}")
        retries -= 1
        time.sleep(0.1)
    return False


def safe_update_last_assistant_image(session_id, image_paths, target_timestamp=None):
    retries = 10
    while retries > 0:
        try:
            data = _read_session(session_id)
            if not data:
                return False
            found = False
            for msg in reversed(data.get("messages", [])):
                if msg["role"] == "assistant":
                    if target_timestamp and abs(msg.get("timestamp", 0) - target_timestamp) > 1.0:
                        continue
                    msg["images"] = image_paths
                    found = True
                    break
            if not found:
                return False
            data["updated_at"] = time.time()
            if _write_session(session_id, data):
                return True
        except Exception as e:
            print(f"Async image update failed: {e}")
        retries -= 1
        time.sleep(0.2 * (11 - retries))
    return False


class AsyncLokiRunner(threading.Thread):
    def __init__(self, session_id, target_img_path, work_dir, session_key, roi_path=None):
        super().__init__()
        self.session_id = session_id
        self.target_img_path = target_img_path
        self.work_dir = work_dir
        self.session_key = session_key
        self.roi_path = roi_path
        self.daemon = True

    def check_cancelled(self):
        cancel_path = os.path.join(CHAT_DIR, self.session_id, "cancel_signal")
        if os.path.exists(cancel_path):
            print(f"[{self.session_id}] Cancellation detected. Aborting.")
            try:
                os.remove(cancel_path)
            except Exception:
                pass
            return True
        return False

    def run(self):
        print(f"[{self.session_id}] AsyncLokiRunner started on {self.target_img_path}")
        tmp_dir = tempfile.mkdtemp()
        loki_out_dir = None
        plot_artifact_dir = None
        plot_artifact_dir_preexisting = False
        try:
            update_progress(self.session_id, 0, "Initializing...")

            try:
                session_data = _read_session(self.session_id) or {}
                last_msg = session_data.get("messages", [{}])[-1]
            except Exception as e:
                print(f"[AsyncLoki] Failed to read session: {e}")
                last_msg = {}

            if self.check_cancelled():
                return

            # 1. Convert to Loki-compatible TIFF
            update_progress(self.session_id, 5, "Converting image...")
            compatible_tiff = os.path.join(tmp_dir, f"loki_input_{os.path.basename(self.target_img_path)}.tiff")
            try:
                try:
                    image_data = tifffile.imread(self.target_img_path)
                except Exception:
                    img_pil = _PILImage.open(self.target_img_path)
                    if img_pil.mode != 'RGB':
                        img_pil = img_pil.convert('RGB')
                    image_data = np.array(img_pil)
                tifffile.imwrite(compatible_tiff, image_data, compression='zlib', tile=(256, 256))
                target_img_for_loki = compatible_tiff
            except Exception as e:
                print(f"Conversion failed ({e}), using original.")
                target_img_for_loki = self.target_img_path

            if self.check_cancelled():
                return

            # 2. Run loki2.sh
            update_progress(self.session_id, 20, "Running Loki (cell detection)...")
            cmd_loki = ["bash", "loki2.sh", target_img_for_loki]
            process = subprocess.Popen(
                cmd_loki, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, universal_newlines=True
            )
            last_update_time = time.time()
            current_progress = 20
            for line in process.stdout:
                if self.check_cancelled():
                    process.kill()
                    return
                print(f"[Loki-{self.session_id}] {line.strip()}")
                now = time.time()
                if now - last_update_time > 1.0:
                    clean_line = line.strip()[:50]
                    if current_progress < 60:
                        current_progress += 0.5
                    update_progress(self.session_id, int(current_progress), f"Loki: {clean_line}")
                    last_update_time = now
            process.wait()
            if process.returncode != 0:
                raise subprocess.CalledProcessError(process.returncode, cmd_loki)

            # 3. Find output
            basename = os.path.splitext(os.path.basename(target_img_for_loki))[0]
            loki_out_dir = f"./outputs/loki_{basename}.tiff"
            json_path = None
            if os.path.exists(loki_out_dir):
                for f in os.listdir(loki_out_dir):
                    if f.endswith("_cells.json"):
                        json_path = os.path.join(loki_out_dir, f)
                        break
            if not json_path:
                raise FileNotFoundError(f"Could not find *_cells.json in {loki_out_dir}")

            if self.check_cancelled():
                return

            # 4. Generate overlay
            update_progress(self.session_id, 70, "Generating overlay...")
            overlay_out = os.path.join(tmp_dir, f"overlay_{basename}.tif")
            plot_artifact_dir = f"./{os.path.basename(os.path.dirname(target_img_for_loki))}"
            plot_artifact_dir_preexisting = os.path.isdir(plot_artifact_dir)
            cmd_plot = [
                "conda", "run", "-n", "biogis",
                "python", "plot_annotation.py",
                "--image", target_img_for_loki,
                "--json", json_path,
                "--output", overlay_out
            ]
            subprocess.run(cmd_plot, check=True)

            # 5. Store results
            update_progress(self.session_id, 80, "Storing results...")
            session_results_dir = os.path.join(CHAT_DIR, self.session_id)
            os.makedirs(session_results_dir, exist_ok=True)
            overlay_local = os.path.join(session_results_dir, f"overlay_{basename}.tif")
            shutil.copy2(overlay_out, overlay_local)

            cell_types_arg = []
            possible_json_path = os.path.join(plot_artifact_dir, "present_cell_types.json")
            if os.path.exists(possible_json_path):
                cell_types_arg = ["--cell_types_json", possible_json_path]

            overlay_crop_path = None
            if self.roi_path and os.path.exists(self.roi_path):
                try:
                    update_progress(self.session_id, 85, "Cropping overlay...")
                    crop_filename = f"overlay_crop_{basename}_{int(time.time())}.png"
                    local_crop = os.path.join(tmp_dir, crop_filename)
                    if crop_image_by_roi(overlay_out, self.roi_path, local_crop):
                        crops_dir = os.path.join(session_results_dir, "crops")
                        os.makedirs(crops_dir, exist_ok=True)
                        persistent_crop = os.path.join(crops_dir, crop_filename)
                        shutil.copy2(local_crop, persistent_crop)
                        overlay_crop_path = persistent_crop
                except Exception as e:
                    print(f"[AsyncLoki] Crop failed: {e}")

            # 6. Run preprocess
            update_progress(self.session_id, 90, "Finalizing database...")
            preprocess_slots = last_msg.get("preprocess_slots", {})
            overlay_dest_args = []
            if preprocess_slots:
                overlay_file_slot = preprocess_slots.get("overlay_file")
                if overlay_file_slot and overlay_file_slot.get("key"):
                    overlay_dest_args = ["--output_overlay", overlay_file_slot["key"]]

            cmd_pre = [
                "conda", "run", "-n", "mjolnir",
                "python", "run_preprocess.py",
                "--work_dir", self.work_dir,
                "--image", overlay_out,
                "--mode", "overlay"
            ] + cell_types_arg + overlay_dest_args

            res = subprocess.run(cmd_pre, capture_output=True, text=True)
            if res.returncode != 0:
                final_msg = f"Analysis failed during database update.\n{res.stderr or res.stdout}"
            else:
                update_progress(self.session_id, 100, "Analysis complete")
                final_msg = "Loki Analysis Complete. Overlay available."

            # 7. Update session
            assistant_msg = {
                "role": "assistant",
                "content": final_msg,
                "timestamp": time.time(),
                "source": "async_loki_runner"
            }
            if overlay_crop_path:
                assistant_msg["images"] = [overlay_crop_path]
                assistant_msg["content"] += "\n(Overlay crop attached for review)"
            safe_update_session(self.session_id, assistant_msg)

        except Exception as e:
            import traceback
            traceback.print_exc()
            update_progress(self.session_id, 100, "Analysis failed")
            safe_update_session(self.session_id, {
                "role": "assistant",
                "content": f"Loki Analysis Failed: {e}",
                "timestamp": time.time(),
                "source": "async_loki_runner"
            })
        finally:
            cleanup_dirs = [tmp_dir, loki_out_dir]
            if plot_artifact_dir and not plot_artifact_dir_preexisting:
                cleanup_dirs.append(plot_artifact_dir)
            seen = set()
            for d in cleanup_dirs:
                if not d:
                    continue
                d = os.path.abspath(d)
                if d in seen:
                    continue
                seen.add(d)
                if os.path.isdir(d):
                    try:
                        shutil.rmtree(d)
                    except Exception:
                        pass


def process_session(session_id):
    print(f"DEBUG: Worker started for session {session_id}")
    tmp_dir = tempfile.mkdtemp()
    try:
        data = _read_session(session_id)
        if not data:
            return

        messages = data.get("messages", [])
        if not messages:
            return

        last_msg = messages[-1]
        if last_msg.get("role") != "user":
            return

        user_text = last_msg.get("content", "")
        print(f"[{session_id}] Processing: {user_text[:50]}...")

        processed_images = []
        local_full_images = []

        src_images = last_msg.get("src_images") or last_msg.get("images")
        if src_images:
            for idx, img_path in enumerate(src_images):
                if not os.path.exists(img_path):
                    print(f"Image not found: {img_path}")
                    continue
                local_full_images.append(img_path)
                roi_path = last_msg.get("roi_path")
                if roi_path and os.path.exists(roi_path):
                    local_crop = os.path.join(tmp_dir, f"crop_{idx}.png")
                    if crop_image_by_roi(img_path, roi_path, local_crop):
                        processed_images.append(local_crop)

        inference_messages = copy.deepcopy(messages)
        if processed_images:
            if inference_messages and inference_messages[-1].get("role") == "user":
                inference_messages[-1]["images"] = processed_images
        elif "images" in last_msg and not processed_images:
            if inference_messages and inference_messages[-1].get("role") == "user":
                inference_messages[-1].pop("images", None)

        from concurrent.futures import ThreadPoolExecutor

        def do_inference():
            if "run loki analysis" in user_text.lower():
                if local_full_images:
                    work_dir = last_msg.get("work_dir")
                    if work_dir:
                        roi_path_for_loki = last_msg.get("roi_path")
                        runner = AsyncLokiRunner(session_id, local_full_images[0], work_dir, session_id, roi_path_for_loki)
                        runner.start()
                        update_progress(session_id, 1, "Starting background worker...")
                        return "Loki Analysis Started in background. You can continue chatting.", False, 0
                    else:
                        return "Error: 'work_dir' missing.", False, 0
                else:
                    return "No images found to analyze.", False, 0

            timestamp = time.time()
            safe_update_session(session_id, {
                "role": "assistant",
                "content": "...",
                "timestamp": timestamp,
                "source": "hpc_worker",
                "streaming": True
            })

            full_text = ""
            stream_path = os.path.join(CHAT_DIR, session_id, "stream.txt")
            last_write = time.time()
            try:
                model_provider = last_msg.get("model_provider", "ollama")
                selected_model = last_msg.get("model")
                for chunk in run_model_inference(inference_messages, provider=model_provider, model_name=selected_model):
                    full_text += chunk
                    if time.time() - last_write > 0.05:
                        try:
                            with open(stream_path, 'w') as sf:
                                sf.write(full_text)
                            last_write = time.time()
                        except Exception:
                            pass
                with open(stream_path, 'w') as sf:
                    sf.write(full_text)
            except Exception as e:
                full_text += f"\n[Error during generation: {e}]"

            safe_update_streaming_message(session_id, full_text, streaming=False)
            try:
                os.remove(stream_path)
            except Exception:
                pass
            return full_text, True, timestamp

        def do_persist_crops():
            paths = []
            if processed_images:
                crops_dir = os.path.join(CHAT_DIR, session_id, "crops")
                os.makedirs(crops_dir, exist_ok=True)
                for local_path in processed_images:
                    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
                        persistent = os.path.join(crops_dir, os.path.basename(local_path))
                        try:
                            shutil.copy2(local_path, persistent)
                            paths.append(persistent)
                        except Exception as e:
                            print(f"Failed to persist crop: {e}")
            return paths

        with ThreadPoolExecutor(max_workers=2) as inner_executor:
            future_inf = inner_executor.submit(do_inference)
            future_per = inner_executor.submit(do_persist_crops)

            response_text, msg_already_added, returned_timestamp = future_inf.result()

            reply_timestamp = 0
            if msg_already_added:
                reply_timestamp = returned_timestamp
            elif response_text:
                reply_timestamp = time.time()
                safe_update_session(session_id, {
                    "role": "assistant",
                    "content": response_text,
                    "timestamp": reply_timestamp,
                    "source": "hpc_worker"
                })

            crop_paths = future_per.result()
            if crop_paths and reply_timestamp > 0:
                safe_update_last_assistant_image(session_id, crop_paths, target_timestamp=reply_timestamp)

    except Exception as e:
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        with processing_lock:
            processing_keys.discard(session_id)


def enqueue_chat_job(session_id, model, prompt, images, work_dir, roi_path=None, visible=True):
    session_file = _session_path(session_id)

    existing = _lock_and_read_session(session_file)
    session_data = existing if existing else {"session_id": session_id, "messages": []}

    new_message = {
        "role": "user",
        "content": prompt,
        "src_images": images,
        "timestamp": time.time(),
        "work_dir": work_dir,
        "visible": visible
    }
    new_message["model"] = model
    if isinstance(model, str) and ":" in model:
        provider_name, model_name = model.split(":", 1)
        new_message["model_provider"] = provider_name if provider_name in ("ollama", "openai") else "ollama"
        new_message["model"] = model_name
    else:
        new_message["model_provider"] = "ollama"

    if roi_path:
        new_message["roi_path"] = roi_path

    # Determine preprocess overlay path for run_preprocess.py
    target_sample_id = None
    target_sample_id_file = None
    try:
        args_for_cache = vio.load_json(f'{work_dir}/user/args.json')
        target_sample_id = args_for_cache.get("sampleId", target_sample_id)
        original_filename = args_for_cache.get("fileName")
        if target_sample_id and original_filename:
            target_sample_id_file = f"{target_sample_id}-{original_filename}"
    except Exception as e:
        print(f"Warning: Failed to load args.json for preprocess paths: {e}")

    if target_sample_id_file:
        overlay_key = os.path.join(work_dir, "db", "cache", f"{target_sample_id_file}-gis-blend-cell-type-img.tiff")
        new_message["preprocess_slots"] = {"overlay_file": {"key": overlay_key}}
        print(f"Preprocess overlay path: {overlay_key}")

    session_data["messages"].append(new_message)
    session_data["updated_at"] = time.time()

    _lock_and_write_session(session_file, session_data)

    # Dispatch directly — no separate polling loop needed
    with processing_lock:
        if session_id not in processing_keys:
            processing_keys.add(session_id)
            _bot_executor.submit(process_session, session_id)

    return "queued"


# ----------------------------------------------------------------------------
# DASH APP
# ----------------------------------------------------------------------------


def register_chat_routes(server, token, work_dir):

    # Register purely STATIC routes using the provided token.
    # Checks priority over Dash's internal wildcard routes.
    
    @server.route(f"/app/{token}/chat", methods=["POST"])
    def chat_api():
        # NOTE: 'token' arg is removed because it is hardcoded in route
        print(f"DEBUG: chat_api called for token={token}")
        try:
            data = request.get_json(force=True)
            # FORCE session_id to be calculation token (from command line)
            session_id = token
            
            # --- ROI & Image Handling ---
            images = []
            roi_s3_path = None
            
            try:
                # 1. Get Sample Information & Update Visualization State
                args = vio.load_json(f'{work_dir}/user/args.json')
                
                # Check if frontend sent the active layer (from JS)
                active_layer = data.get("active_layer")
                active_layer_index = None
                if active_layer is not None:
                    print(f"DEBUG: Frontend reported active layer: {active_layer}")
                    try:
                        active_layer_index = int(active_layer)
                    except (TypeError, ValueError):
                        active_layer_text = str(active_layer).strip()
                        if "Image Layer" in active_layer_text:
                            try:
                                active_layer_index = int(active_layer_text.rsplit(" ", 1)[-1]) - 1
                            except (TypeError, ValueError):
                                active_layer_index = None
                        elif active_layer_text in ("Cell Type", "CNV"):
                            active_layer_index = 1
                        else:
                            active_layer_index = None
                    # Persist the actual layer index separately. visualizeOption is
                    # still a text mode elsewhere, so do not overwrite it with 0/1.
                    args['activeLayer'] = active_layer_index
                    vio.dump_json(args, f'{work_dir}/user/args.json')
                
                sample_id = args.get('sampleId', 'default')
                
                # 2. Identify Image Path
                # Image is in work_dir/db/data/{sample_id}-wsi-img.tiff
                image_s3_path = args.get("tutorialImagePath") or f"{work_dir}/db/data/{sample_id}-wsi-img.tiff"
                
                # No more scaling needed - VivViewer handles full resolution pyramids
                
                # Check for active overlay based on visualizeOption
                visual_option = args.get('visualizeOption', 'Original')
                print(f"DEBUG: Active Visual Option: {visual_option}")
                
                if active_layer_index and active_layer_index > 0:
                     sample_id_file = args.get('sampleIdFile')
                     if sample_id_file:
                         overlay_path = f"{work_dir}/db/cache/{sample_id_file}-gis-blend-cell-type-img.tiff"
                         
                         if vio.exists(overlay_path):
                             image_s3_path = overlay_path
                             print(f"DEBUG: Using active layer {active_layer_index} overlay for chat crop: {image_s3_path}")
                         else:
                             print(f"DEBUG: Active layer {active_layer_index} overlay not found: {overlay_path}")
                elif "Cell Type" in visual_option or visual_option == "CNV":
                     # Use "sampleIdFile" which matches the key used in interface.py
                     sample_id_file = args.get('sampleIdFile')
                     if sample_id_file:
                         overlay_path = f"{work_dir}/db/cache/{sample_id_file}-gis-blend-cell-type-img.tiff"
                         
                         if vio.exists(overlay_path):
                             image_s3_path = overlay_path
                             print(f"DEBUG: Using Specific Cell Type Overlay: {image_s3_path}")
                         else:
                             print(f"DEBUG: Specific Overlay not found: {overlay_path}")

                # 3. Handle ROI Upload
                # Try to use coords.json (Pixel Coordinates) first, as generated by save_roi
                user_coords_path = f"{work_dir}/user/coords.json"
                user_roi_path = f"{work_dir}/user/roi.json"
                
                roi_json_to_upload = None
                
                # Check for pixel coordinates first
                if vio.exists(user_coords_path):
                    try:
                        with vio.open_file(user_coords_path, "r") as f_coords:
                            coords_data = json.load(f_coords)
                            # coords.json is a list of lists of points: [ [[x1,y1], [x2,y2]...], ... ]
                            
                            if coords_data:
                                roi_json_to_upload = coords_data
                                print(f"DEBUG: Using coords.json (pixels) for ROI. Num polygons: {len(coords_data)}")

                                # --- OPTIMIZATION START: Check for Duplicates ---
                                is_duplicate = False
                                roi_s3_path = None # Will be filled from cache if duplicate
                                
                                # Paths for cache
                                last_roi_path = f"{work_dir}/user/last_processed_roi.json"
                                last_img_path_files = f"{work_dir}/user/last_processed_image.txt"
                                last_roi_s3_key_file = f"{work_dir}/user/last_roi_s3_key.txt"
                                last_crop_path_file = f"{work_dir}/user/last_crop_path.txt"
                                
                                if vio.exists(last_roi_path) and vio.exists(last_img_path_files):
                                    try:
                                        last_roi_data = vio.load_json(last_roi_path)
                                        with vio.open_file(last_img_path_files, 'r') as f_img:
                                            last_img_path = f_img.read().strip()
                                        
                                        if (last_roi_data == roi_json_to_upload) and (last_img_path == image_s3_path):
                                            print("DEBUG: Smart Persistence -> Duplicate Detected. Attempting to reuse cache.")
                                            is_duplicate = True
                                            
                                            # Try to recover paths
                                            if vio.exists(last_roi_s3_key_file):
                                                 with vio.open_file(last_roi_s3_key_file, 'r') as f:
                                                     roi_s3_path = f.read().strip()
                                            
                                            if vio.exists(last_crop_path_file):
                                                 with vio.open_file(last_crop_path_file, 'r') as f:
                                                     preview_crop_path = f.read().strip() # This var is local to this scope usually, need to ensure it persists
                                                     print(f"DEBUG: Reusing Crop Path: {preview_crop_path}")
                                            
                                            if not roi_s3_path:
                                                print("DEBUG: Cache missing S3 key. Forcing re-upload.")
                                                is_duplicate = False
                                    except Exception as e:
                                        print(f"DEBUG: Cache check failed: {e}")
                                # --- OPTIMIZATION END ---
                                
                                # --- GENERATE CROP PREVIEW ---
                                try:
                                    import tifffile as tf
                                    import numpy as np
                                    import cv2
                                    
                                    # Calculate Bounding Box of all polygons
                                    all_points = []
                                    for poly in coords_data:
                                        all_points.extend(poly)
                                    
                                    if all_points and vio.exists(image_s3_path) and not is_duplicate:
                                        pts = np.array(all_points)
                                        x_min, y_min = np.min(pts, axis=0)
                                        x_max, y_max = np.max(pts, axis=0)
                                        
                                        # No padding, exact ROI
                                        pad = 0
                                        x_min = max(0, int(x_min) - pad)
                                        y_min = max(0, int(y_min) - pad)
                                        x_max = int(x_max) + pad
                                        y_max = int(y_max) + pad
                                        
                                        print(f"DEBUG: Cropping ROI: [{x_min}:{x_max}, {y_min}:{y_max}] from {image_s3_path}")
                                        
                                        # Use VIO to open S3 stream for TiffFile or PIL
                                        with vio.open_file(image_s3_path, "rb") as f_img:
                                            # Initialize crop variable
                                            crop = None
                                            try:
                                                import tifffile as tf
                                                # Try TIFF first
                                                with tf.TiffFile(f_img) as tif:
                                                    page = tif.pages[0]
                                                    ih, iw = page.shape[0], page.shape[1]
                                                    x_max = min(x_max, iw)
                                                    y_max = min(y_max, ih)
                                                    
                                                    if x_max > x_min and y_max > y_min:
                                                        crop = page.asarray()[y_min:y_max, x_min:x_max]
                                            except Exception as e_tif:
                                                print(f"DEBUG: Not a TIFF or Tifffile failed: {e_tif}. Trying PIL.")
                                                # Fallback to PIL (Pillow) for JPEG/PNG
                                                try:
                                                    from PIL import Image
                                                    f_img.seek(0)
                                                    img = Image.open(f_img)
                                                    iw, ih = img.size
                                                    x_max = min(x_max, iw)
                                                    y_max = min(y_max, ih)
                                                    
                                                    if x_max > x_min and y_max > y_min:
                                                        crop_pil = img.crop((x_min, y_min, x_max, y_max))
                                                        crop = np.array(crop_pil)
                                                except Exception as e_pil:
                                                    print(f"DEBUG: PIL fallback failed: {e_pil}")

                                            if crop is not None and crop.size > 0:
                                                # Save as PNG for preview
                                                crop_name = f"roi_crop_{int(time.time())}.png"
                                                crop_dir = f"{work_dir}/user/crops" 
                                                vio.ensure_dir(crop_dir)
                                                crop_path = f"{crop_dir}/{crop_name}"
                                                
                                                if len(crop.shape) == 3:
                                                    if crop.shape[2] == 4:
                                                        crop = cv2.cvtColor(crop, cv2.COLOR_RGBA2BGR)
                                                    elif crop.shape[2] == 3:
                                                        crop = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
                                                elif len(crop.shape) == 2:
                                                     # Grayscale to BGR
                                                     crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)

                                                vio.write_image(crop_path, crop)
                                                
                                                # Verify it exists before setting it as the preview path
                                                if vio.exists(crop_path):
                                                    print(f"DEBUG: Saved Crop to {crop_path}")
                                                    roi_s3_path = crop_path 
                                                    preview_crop_path = crop_path
                                                else:
                                                    print(f"DEBUG: Failed to write crop to {crop_path}")
                                                    
                                except Exception as e:
                                    print(f"DEBUG: Failed to generate crop: {e}")

                    except Exception as e:
                        print(f"DEBUG: Failed to convert coords.json: {e}")

                # Fallback to roi.json if coords.json failed or didn't exist
                # Only use preview_crop_path if it was set in the block above
                if 'preview_crop_path' not in locals():
                     preview_crop_path = None
                
                if not roi_json_to_upload and vio.exists(user_roi_path):
                    try:
                        with vio.open_file(user_roi_path, "r") as f_src:
                            roi_json = json.load(f_src)
                            # Basic validation
                            if ("features" in roi_json and roi_json["features"]) or "geometry" in roi_json:
                                roi_json_to_upload = roi_json
                                print(f"DEBUG: Using original roi.json (Lat/Lon)")
                                
                                # --- FALLBACK CROP GENERATION ---
                                # Load coords.json (Pixels) to generate crop
                                coords_path = f"{work_dir}/user/coords.json"
                                
                                if vio.exists(coords_path) and vio.exists(image_s3_path):
                                    try:
                                        import tifffile as tf
                                        import numpy as np
                                        import cv2
                                        
                                        coords_data = vio.load_json(coords_path)
                                        # coords_data is typically [[[x,y],...], ...]
                                        
                                        all_points = []
                                        for poly in coords_data:
                                            all_points.extend(poly)
                                        
                                        if all_points:
                                            pts = np.array(all_points)
                                            # Sanity check: If coordinates are < 360, they might be LatLng?
                                            # But coords.json is supposed to be Pixels.
                                            x_min, y_min = np.min(pts, axis=0)
                                            x_max, y_max = np.max(pts, axis=0)
                                            
                                            # No padding
                                            pad = 0
                                            x_min = max(0, int(x_min) - pad)
                                            y_min = max(0, int(y_min) - pad)
                                            x_max = int(x_max) + pad
                                            y_max = int(y_max) + pad
                                            
                                            # Use VIO to open S3 stream for TiffFile or PIL
                                            with vio.open_file(image_s3_path, "rb") as f_img:
                                                # Initialize crop variable
                                                crop = None
                                                try:
                                                    import tifffile as tf
                                                    # Try TIFF first
                                                    with tf.TiffFile(f_img) as tif:
                                                        page = tif.pages[0]
                                                        ih, iw = page.shape[0], page.shape[1]
                                                        x_max = min(x_max, iw)
                                                        y_max = min(y_max, ih)
                                                        
                                                        if x_max > x_min and y_max > y_min:
                                                            crop = page.asarray()[y_min:y_max, x_min:x_max]
                                                except Exception as e_tif:
                                                    print(f"DEBUG: Fallback Not a TIFF or Tifffile failed: {e_tif}. Trying PIL.")
                                                    # Fallback to PIL (Pillow) for JPEG/PNG
                                                    try:
                                                        from PIL import Image
                                                        f_img.seek(0)
                                                        img = Image.open(f_img)
                                                        iw, ih = img.size
                                                        x_max = min(x_max, iw)
                                                        y_max = min(y_max, ih)
                                                        
                                                        if x_max > x_min and y_max > y_min:
                                                            crop_pil = img.crop((x_min, y_min, x_max, y_max))
                                                            crop = np.array(crop_pil)
                                                    except Exception as e_pil:
                                                        print(f"DEBUG: Fallback PIL failed: {e_pil}")

                                                if crop is not None and crop.size > 0:
                                                    crop_name = f"roi_crop_{int(time.time())}.png"
                                                    crop_dir = f"{work_dir}/user/crops" 
                                                    vio.ensure_dir(crop_dir)
                                                    crop_path = f"{crop_dir}/{crop_name}"
                                                    
                                                    # Handle Color Conversion (RGB/RGBA -> BGR)
                                                    if len(crop.shape) == 3:
                                                        if crop.shape[2] == 4:
                                                            crop = cv2.cvtColor(crop, cv2.COLOR_RGBA2BGR)
                                                        elif crop.shape[2] == 3:
                                                            crop = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
                                                    if len(crop.shape) == 3 and crop.shape[2] >= 3:
                                                         crop = cv2.cvtColor(crop, cv2.COLOR_RGB2BGR)
                                                    
                                                    vio.write_image(crop_path, crop)
                                                    
                                                    if vio.exists(crop_path):
                                                        print(f"DEBUG: Fallback Saved Crop to {crop_path}")
                                                        preview_crop_path = crop_path
                                                        # Update Cache for Crop
                                                        try:
                                                            with vio.open_file(last_crop_path_file, 'w') as f:
                                                                f.write(preview_crop_path)
                                                        except: pass
                                                    else:
                                                        print(f"DEBUG: Failed to write fallback crop to {crop_path}")

                                    except Exception as e_crop:
                                        print(f"DEBUG: Fallback crop failed: {e_crop}")
                                        
                    except Exception as e:
                        print(f"DEBUG: Failed to parse roi.json: {e}")

                # Optimization & Logic: Check for changes in ROI OR Image Source
                last_roi_path = f"{work_dir}/user/last_processed_roi.json"
                last_img_path_files = f"{work_dir}/user/last_processed_image.txt"
                
                send_image = False
                send_roi = False
                
                # Only check logic if we HAVE an ROI. If no ROI, we never send image (per user request).
                if roi_json_to_upload:
                    # Default assumption: Sending both
                    send_image = True
                    send_roi = True
                    
                    # Check optimization
                    if vio.exists(last_roi_path) and vio.exists(last_img_path_files):
                        try:
                            last_roi_data = vio.load_json(last_roi_path)
                            with vio.open_file(last_img_path_files, 'r') as f_img:
                                last_img_path = f_img.read().strip()
                            
                            # If BOTH ROI and Image are identical, skip everything (History covers it)
                            # LOGIC MOVED TO TOP (Smart Persistence)
                            pass
                        except Exception as e:
                            print(f"DEBUG: Optimization check failed: {e}")
                
                    # Update State if we are proceeding
                    if send_roi or send_image:
                         vio.dump_json(roi_json_to_upload, last_roi_path)
                         with vio.open_file(last_img_path_files, 'w') as f_img:
                             f_img.write(image_s3_path)
                
                else:
                    # ROI Removed/Empty -> Clear state so next valid ROI triggers update
                    if vio.exists(last_roi_path): vio.remove(last_roi_path)
                    if vio.exists(last_img_path_files): vio.remove(last_img_path_files)
                    print("DEBUG: No ROI selected. Sending text only (No Image).")

                # Attach Image if needed
                # Relaxed check: Trust S3 paths to avoid vio.exists overhead/failure
                if send_image and (image_s3_path and (image_s3_path.startswith("s3://") or vio.exists(image_s3_path))):
                     images.append(image_s3_path)
                     print(f"DEBUG: Attaching Image: {image_s3_path}")

                # Upload if we have something (New/Changed ROI)
                if roi_json_to_upload:
                    if is_duplicate and roi_s3_path:
                         print(f"DEBUG: Skipping Save (Duplicate). Reusing: {roi_s3_path}")
                    else:
                        try:
                            roi_name = f"roi_{int(time.time())}.json"
                            roi_local_dir = os.path.join(CHAT_DIR, session_id)
                            os.makedirs(roi_local_dir, exist_ok=True)
                            roi_local_path = os.path.join(roi_local_dir, roi_name)
                            with open(roi_local_path, 'w') as f:
                                json.dump(roi_json_to_upload, f)
                            roi_s3_path = roi_local_path
                            print(f"DEBUG: Saved ROI to {roi_local_path}")
                            try:
                                with vio.open_file(last_roi_s3_key_file, 'w') as f:
                                    f.write(roi_local_path)
                            except: pass
                        except Exception as e:
                             print(f"DEBUG: Failed to save ROI: {e}")

                else:
                    # If we skipped upload (optimization) or had no ROI:
                    if not images:
                         print("DEBUG: No new ROI/Image sent (Optimization active).")
                    else:
                         print(f"DEBUG: No valid ROI found. Using full image context.")
            
            except Exception as e:
                print(f"ERROR handling ROI/Image: {e}")
                # We continue without crashing
            
            # ----------------------------

            prompt = data.get("prompt", "")
            try:
                if 'roi_json_to_upload' in locals() and roi_json_to_upload:
                    roi_gene_context = build_roi_gene_context(work_dir, roi_json_to_upload)
                    if roi_gene_context:
                        prompt = prompt + roi_gene_context
            except Exception as e:
                print(f"DEBUG: Failed to add spatial omics context: {e}")

            status = enqueue_chat_job(
                session_id=session_id,
                model=data.get("model", "qwen2.5vl:72b"),
                prompt=prompt,
                images=images,
                work_dir=work_dir,
                    roi_path=roi_s3_path # Pass the explicit ROI path
            )
            
            # Decide what to show as preview:
            # If we have a specific overlay (Cell Type), use that.
            # Otherwise use the main WSI.
            # ensuring we don't send the JSON path as the image preview
            if 'preview_crop_path' in locals() and preview_crop_path:
                 preview_img = preview_crop_path
            else:
                 # User requested to ONLY show preview if an area is selected.
                 # So we disable the full image fallback.
                 preview_img = None
            
            return jsonify({
                "status": status,
                "images": images,          # Full image list
                "roi_image": preview_img   # The Image file (TIFF), not the JSON
            })
        except Exception as e:
            print(f"ERROR in chat_api: {e}")
            return jsonify({"status": "error", "message": str(e)})

    @server.route(f"/app/{token}/chat/poll", methods=["GET"])
    def chat_poll_api():
        try:
            session_id = token
            session_file = _session_path(session_id)
            try:
                current_data = _lock_and_read_session(session_file)
                if current_data:
                    messages = current_data.get("messages", [])
                    if messages:
                        last_msg = messages[-1]
                        if last_msg.get("role") == "assistant":
                            is_streaming = last_msg.get("streaming", False)
                            status = "streaming" if is_streaming else "done"
                            response_content = last_msg.get("content", "")
                            if is_streaming:
                                try:
                                    stream_path = os.path.join(CHAT_DIR, session_id, "stream.txt")
                                    if os.path.exists(stream_path):
                                        with open(stream_path) as sf:
                                            stream_text = sf.read()
                                        if stream_text:
                                            response_content = stream_text
                                except Exception:
                                    pass
                            return jsonify({
                                "status": status,
                                "response": response_content,
                                "images": last_msg.get("images", []),
                                "visible": last_msg.get("visible", True)
                            })
            except Exception as e:
                print(f"Poll check error: {e}")
                return jsonify({"status": "error", "message": str(e)})
            return jsonify({"status": "processing"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"API Error: {str(e)}"})

    @server.route(f"/app/{token}/chat/clear", methods=["POST"])
    def clear_session_api():
        try:
            session_id = token
            errors = []

            # ── 1. Local chat session dir ──
            try:
                session_dir = os.path.join(CHAT_DIR, session_id)
                if os.path.isdir(session_dir):
                    shutil.rmtree(session_dir)
                    print(f"[chat/clear] Chat session dir deleted for {session_id}")
            except Exception as e:
                errors.append(f"chat session dir: {e}")

            # ── 2. OME-TIFF conversion cache ──
            try:
                user_cache_dir = os.path.join(TMP_BASE, "ome_tiff_cache", token)
                if os.path.exists(user_cache_dir):
                    shutil.rmtree(user_cache_dir)
                    print(f"[chat/clear] OME-TIFF cache wiped for {token}")
            except Exception as e:
                errors.append(f"OME-TIFF cache: {e}")

            # ── 3. work_dir/user/ (ROI state, crop files, caches) ──
            try:
                user_state_dir = os.path.join(work_dir, "user")
                if os.path.isdir(user_state_dir):
                    for entry in os.listdir(user_state_dir):
                        entry_path = os.path.join(user_state_dir, entry)
                        if os.path.isfile(entry_path):
                            os.remove(entry_path)
                        elif os.path.isdir(entry_path):
                            shutil.rmtree(entry_path)
                    print(f"[chat/clear] work_dir/user/ wiped for {token}")
            except Exception as e:
                errors.append(f"work_dir/user/: {e}")

            # ── 4. work_dir/data_input_temp/tmp/ (upload staging) ──
            try:
                tmp_upload_dir = os.path.join(work_dir, "data_input_temp", "tmp")
                if os.path.isdir(tmp_upload_dir):
                    shutil.rmtree(tmp_upload_dir)
                    os.makedirs(tmp_upload_dir, exist_ok=True)
                    print(f"[chat/clear] Upload tmp dir wiped for {token}")
            except Exception as e:
                errors.append(f"upload tmp: {e}")

            if errors:
                return jsonify({"status": "partial", "message": "Session cleared with warnings", "warnings": errors})
            return jsonify({"status": "success", "message": "Session cleared"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})
    def prune_ome_tiff_cache(cache_dir, max_gb=10):
        """Removes oldest files globally across all user subdirectories if total size exceeds limit."""
        try:
            if not os.path.exists(cache_dir): return
            all_files = []
            for root, dirs, files in os.walk(cache_dir):
                for f in files:
                    p = os.path.join(root, f)
                    if os.path.isfile(p):
                        all_files.append((os.path.getmtime(p), p, os.path.getsize(p)))
            
            all_files.sort() # Oldest first
            total_size = sum(f[2] for f in all_files)
            limit = max_gb * 1024 * 1024 * 1024
            
            while total_size > limit and all_files:
                mtime, p, size = all_files.pop(0)
                try:
                    os.remove(p)
                    total_size -= size
                    print(f"[ome_tiff] Global Prune: Removed {p}")
                except: pass
        except Exception as e:
            print(f"[ome_tiff] Global pruning error: {e}")

    @server.route(f"/app/{token}/ome_tiff")
    def serve_ome_tiff():
        path = request.args.get("path")
        if not path:
            return "Missing path", 400
        path = urllib.parse.unquote(path)

        try:
            parent_cache_dir = os.path.join(TMP_BASE, "ome_tiff_cache")
            prune_ome_tiff_cache(parent_cache_dir, max_gb=10)
            ome_local_path = ensure_ome_tiff_cached(path, token)
            return send_file(ome_local_path, conditional=True, mimetype='image/tiff')
        except FileNotFoundError:
            return f"Not found: {path}", 404

        except Exception as e:
            print(f"OME-TIFF Serve Error: {e}")
            import traceback; traceback.print_exc()
            return f"Error reading file: {str(e)}", 500

    @server.route(f"/app/{token}/preview")
    def preview_image():
        import cv2
        import numpy as np
        import io
        
        path = request.args.get("path")
        if not path:
            return "Missing path", 400
        path = urllib.parse.unquote(path)
        
        try:
            # Handle HTTPS S3 URLs (Convert to s3:// for vio)
            if path.startswith("https://") and ".s3." in path and "amazonaws.com" in path:
                try:
                    # Format: https://{BUCKET}.s3.{REGION}.amazonaws.com/{KEY}
                    # Split by amazonaws.com/ to get Key
                    parts = path.split("amazonaws.com/")
                    if len(parts) > 1:
                        key = parts[1]
                        # Extract bucket from domain
                        domain_parts = parts[0].split(".s3.")
                        if len(domain_parts) > 0:
                            bucket = domain_parts[0].replace("https://", "")
                            # Reconstruct as s3://
                            path = f"s3://{bucket}/{key}"
                            print(f"DEBUG: Converted HTTPS URL to s3:// URI: {path}")
                except Exception as e:
                    print(f"Warning: Failed to parse HTTPS S3 URL: {e}")

            # Use vio to handle both S3 and local paths
            if vio.exists(path):
                # vio.open_file returns a file-like object
                with vio.open_file(path, 'rb') as f:
                    file_content = f.read()

                # Convert TIFF/Large images to PNG Thumbnail
                # 1. Decode
                nparr = np.frombuffer(file_content, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                
                if img is None:
                    # Fallback for non-image files or failures
                     return send_file(io.BytesIO(file_content), mimetype="application/octet-stream")

                # 2. Resize if too big (Thumbnail generation)
                h, w = img.shape[:2]
                max_dim = 512
                if h > max_dim or w > max_dim:
                    scale = max_dim / max(h, w)
                    new_w = int(w * scale)
                    new_h = int(h * scale)
                    img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)
                
                # 3. Encode to PNG
                _, img_encoded = cv2.imencode('.png', img)
                return send_file(io.BytesIO(img_encoded.tobytes()), mimetype="image/png")
            else:
                return f"Not found: {path}", 404
        except Exception as e:
            print(f"Preview Error: {e}")
            return f"Error reading file: {str(e)}", 500

def main():
    # Setup paths and IDs
    VALID_TOKEN = args.token
    work_dir = args.wd
    folder_id = ""
    app_dir = os.path.dirname(os.path.realpath(__file__))

    # Initialize Flask Server explicitly
    from flask import Flask
    server = Flask(__name__)
    CORS(server) # Enable CORS for all routes (Fixes Tile Loading Status 0)

    # Register Chat Routes BEFORE Dash
    # Pass server AND specific token AND work_dir
    register_chat_routes(server, VALID_TOKEN, work_dir)

    # Initialize Dash app
    app = dash.Dash(
        __name__,
        server=server,
        meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
        requests_pathname_prefix=f"/app/{VALID_TOKEN}/",
        routes_pathname_prefix=f"/app/{VALID_TOKEN}/",
        suppress_callback_exceptions=True
    )
    
    # (localtileserver removed - VivViewer serves OME-TIFFs via /ome_tiff proxy)

    # Configure app
    temp_dir = f"{work_dir}/data_input_temp/tmp/"
    app.title = "Spatial Omics Copilot"
    prepare_file_folder(folder_id, work_dir)
    update_data_cache(folder_id, work_dir)
    dump_default_para_arg(folder_id, work_dir)

    # Save token mapping
    user_token_info = {
        "user-port": args.port,
        "user-token": args.token,
        "tile-port": int(args.port)+1,
    }
    add_token_mapping(work_dir, folder_id, user_token_info)

    add_token_mapping(work_dir, folder_id, user_token_info)

    app.layout = create_layout(work_dir, folder_id)

    # ----------------------- Local Chunked Upload -----------------------
    @app.server.route(f"/app/{args.token}/upload_chunk", methods=['POST'])
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


    @app.server.route(f"/app/{args.token}/upload_status/<job_id>", methods=["GET"])
    def get_upload_status(job_id):
        return jsonify(status_store.get_status(job_id))

    # ----------------------- Upload Callbacks -----------------------

    # Callback to handle upload of H&E image
    # Callback to handle upload of H&E image
    # Callback to handle upload of H&E image
    # Callback to handle upload of H&E image
    # @app.callback(
    #     Output('status1', 'children'),
    #     Input('upload-data-image-result', 'value')
    # )
    # def callback_upload_image(filenames_upload_image):
    #     if not filenames_upload_image:
    #         return dash.no_update
    #     return upload_image([filenames_upload_image], folder_id, work_dir, app_dir)

    # Callback to handle upload of additional spot data
    # Callback to handle upload of additional spot data
    @app.callback(
        Output('gene-dropdown-spot', 'children'),
        Input('upload-data-addition-spot-result', 'value')
    )
    def callback_upload_spot_data(filenames_upload_spot_data):
        if not filenames_upload_spot_data:
            return dash.no_update
        return upload_spatial_h5ad([filenames_upload_spot_data], folder_id, work_dir)

    # Callback to handle upload of additional cell data
    # Callback to handle upload of additional cell data
    @app.callback(
        Output('gene-dropdown-cell', 'children'),
        Input('upload-data-addition-cell-result', 'value')
    )
    def callback_upload_cell_data(filenames_upload_cell_data):
        if not filenames_upload_cell_data:
            return dash.no_update
        upload_cell = upload_cell_data([filenames_upload_cell_data], folder_id, work_dir)
        return upload_cell
    
    # Callback to handle upload of additional cell data detection
    # Callback to handle upload of additional cell data detection
    @app.callback(
        Output('cell-detection-confirm', 'children'),
        Input('upload-data-addition-cell-detection-result', 'value')
    )
    def callback_upload_cell_data_detection(filenames_upload_cell_data):
        if not filenames_upload_cell_data:
            return dash.no_update
        upload_cell = upload_cell_data([filenames_upload_cell_data], folder_id, work_dir)
        calculation_cell_detection(folder_id, work_dir)
        return upload_cell

    # Callback to handle upload of pathway data
    # Callback to handle upload of pathway data
    @app.callback(
        Output('pathway-dropdown', 'children'),
        Input("upload-data-pathway-result", "value")
    )
    def callback_upload_pathway(filenames_upload_pathway):
        if not filenames_upload_pathway:
            return dash.no_update
        return upload_pathway([filenames_upload_pathway], folder_id, work_dir)

    # Callback to handle upload of CNV data
    # Callback to handle upload of CNV data
    @app.callback(
        Output('status7', 'children'),
        Input("upload-data-cnv-result", "value")
    )
    def callback_upload_cnv(filenames_upload_cnv):
        if not filenames_upload_cnv:
            return dash.no_update
        return upload_cnv([filenames_upload_cnv], folder_id, work_dir)

    # Callback to handle upload of coordinate data
    # Callback to handle upload of coordinate data
    @app.callback(
        Output('status9', 'children'),
        Input("upload-data-coor-result", 'value')
    )
    def callback_upload_coordinate(filenames_upload_coor):
        if not filenames_upload_coor:
            return dash.no_update
        return upload_coordinate([filenames_upload_coor], folder_id, work_dir)


    # ----------------------- UI State Callbacks -----------------------

    # Toggle callback removed to prevent conflict with client-side JS
    # @app.callback(
    #     Output("submit-wrapper", "className"),
    #     Input("toggle-submit-btn", "n_clicks"),
    #     prevent_initial_call=True
    # )
    # def toggle_submit_panel(n):
    #     return "collapsed" if n % 2 == 1 else ""

    # Show upload section based on data type
    @app.callback(
        Output('additional-data-box', 'children'),
        Input('spot-cell-option', 'data')
    )
    def callback_show_cell_spot_upload(spot_cell_option):
        return show_cell_spot_upload(spot_cell_option, folder_id, work_dir)

    # Update dropdown content for data type and visualization option
    @app.callback(
        Output('visualize-data-upload', 'children'),
        Input('spot-cell-option', 'data'),
        Input('visual-type-container', 'data')
    )
    def callback_update_output_visual(spot_cell_option, visualize_option):
        return update_output_visual(spot_cell_option, visualize_option, folder_id, work_dir)

    # Highlight selected button for spot/cell toggle
    @app.callback(
        Output({'type': 'spot-cell-btn', 'index': ALL}, 'className'),
        Input('spot-cell-option', 'data'),
        State({'type': 'spot-cell-btn', 'index': ALL}, 'id')
    )
    def toggle_spot_cell_highlight(selected_value, ids):
        return ["toggle-btn active" if btn['index'] == selected_value else "toggle-btn" for btn in ids]

    # Highlight selected visualization type button
    @app.callback(
        Output({'type': 'vis-type-btn', 'index': ALL}, 'className'),
        Input('visual-type-container', 'data'),
        State({'type': 'vis-type-btn', 'index': ALL}, 'id')
    )
    def toggle_visual_type_highlight(selected_value, ids):
        return [
            "toggle-btn active" if btn['index'] == selected_value else "toggle-btn"
            for btn in ids
        ]

    # ----------------------- Upload Toggle Callback -----------------------
    # @app.callback(
    #     [Output("custom-uploader-container", "style"),
    #      Output("standard-uploader-container", "style")],
    #     Input("upload-mode-toggle", "value")
    # )
    # def toggle_uploader_visibility(selected_values):
    #     if "standard" in selected_values:
    #         return {"display": "none"}, {"display": "block"}
    #     return {"display": "block"}, {"display": "none"}

    # ----------------------- Standard Dash Callback (Custom Uploader) -----------------------
    # Replaced @du.callback because we are using custom JS/HTML uploader now
    @app.callback(
        [Output('status1', 'children'),
         Output('processing-job-id', 'data'),
         Output('start-loki-analysis-btn', 'disabled')],
        [Input('upload-data-image-result-dash', 'value')]
    )
    def callback_on_completion(upload_path):
        print(f"DEBUG: callback_on_completion ENTRY: path={upload_path}", flush=True)
        
        if not upload_path:
            print("DEBUG: upload_path is empty, ignoring.", flush=True)
            return dash.no_update, dash.no_update, dash.no_update

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
                return "Tutorial image ready. Click Re-visualize Image.", "tutorial", False
            except Exception as e:
                print(f"ERROR preparing tutorial image: {e}", flush=True)
                status_store.update_status("tutorial", 0, f"Error: {str(e)}")
                return f"Tutorial image error: {e}", "tutorial", True

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
        
        # Loki temporarily disabled — keep button disabled after upload too
        return "File uploaded. Starting processing...", job_id, True

    # ----------------------- Check Existing File Callback -----------------------
    @app.callback(
        Output('start-loki-analysis-btn', 'disabled', allow_duplicate=True),
        [Input('url', 'pathname'),
         Input('visual-input', 'n_clicks')],
        prevent_initial_call='initial_duplicate'
    )
    def check_loki_btn_state(pathname, n_clicks):
        # Loki temporarily disabled
        return True
        try:
             # Look for args.json to get sampleId
             # work_dir and folder_id are available in main() scope
             # Note: folder_id is usually empty string based on current code
             args_path = f'{work_dir}/user{folder_id}/args.json'
             if vio.exists(args_path):
                 args_json = vio.load_json(args_path)
                 if args_json.get("tutorialImagePath"):
                     print("DEBUG: Tutorial image is active. Enabling Start Loki Button.")
                     return False
                 sample_id = args_json.get('sampleId')
                 if sample_id:
                     # Check if image exists in S3
                     image_s3_path = f"{work_dir}/db/data/{sample_id}-wsi-img.tiff"
                     if vio.exists(image_s3_path):
                         print(f"DEBUG: Image found at {image_s3_path}. Enabling Start Loki Button.")
                         return False # Disabled = False -> Enabled
                     else:
                         print(f"DEBUG: Image NOT found at {image_s3_path}.")
        except Exception as e:
            print(f"DEBUG: Error checking loki button state: {e}")
        
        return dash.no_update

    # ----------------------- Progress Bar Callback -----------------------
    # ----------------------- Progress Bar Callback -----------------------
    # DISABLED: Frontend s3_upload.js now handles the UI updates via polling /upload_status/<job_id>
    # @app.callback(
    #     [Output('processing-status-container', 'children'),
    #      Output('processing-interval', 'disabled', allow_duplicate=True)],
    #     Input('processing-interval', 'n_intervals'),
    #     State('processing-job-id', 'data'),
    #     prevent_initial_call=True
    # )
    # def update_preprocessing_status(n, job_id):
    #     if not job_id:
    #         return dash.no_update, True
    #         
    #     status_data = status_store.get_status(job_id)
    #     # status example: {'progress': 50, 'message': 'Processing...', 'error': None}
    #     
    #     progress = status_data.get('progress', 0)
    #     message = status_data.get('message', 'Waiting...')
    #     error = status_data.get('error')
    #     
    #     # Create Progress Bar UI
    #     bar_color = "#4CAF50" if not error else "#F44336"
    #     if error:
    #         message = f"Error: {error}"
    #         
    #     bar_ui = html.Div([
    #         html.Div(message, style={'marginBottom': '5px', 'fontSize': '14px', 'fontWeight': '500'}),
    #         html.Div(
    #             html.Div(style={'width': f'{progress}%', 'height': '100%', 'backgroundColor': bar_color, 'transition': 'width 0.5s'}),
    #             style={'width': '100%', 'height': '20px', 'backgroundColor': '#e0e0e0', 'borderRadius': '10px', 'overflow': 'hidden'}
    #         )
    #     ])
    #     
    #     # Stop polling if complete or error
    #     stop_interval = False
    #     if progress >= 100 or error:
    #         stop_interval = True
    #         
    #     return bar_ui, stop_interval

    # Update selected value for data type (spot or cell)
    @app.callback(
        Output('spot-cell-option', 'data'),
        Input({'type': 'spot-cell-btn', 'index': ALL}, 'n_clicks'),
        State({'type': 'spot-cell-btn', 'index': ALL}, 'id')
    )
    def update_spot_cell_value(n_clicks, ids):
        if not any(n_clicks):
            return dash.no_update
        return ctx.triggered_id['index']

    # Update selected value for visualization type
    @app.callback(
        Output('visual-type-container', 'data'),
        Input({'type': 'vis-type-btn', 'index': ALL}, 'n_clicks'),
        State({'type': 'vis-type-btn', 'index': ALL}, 'id'),
        State('visual-type-container', 'data'),
        prevent_initial_call=True
    )
    def update_visual_type_value(n_clicks, ids, current_value):
        if not any(n_clicks):
            return dash.no_update

        triggered_id = ctx.triggered_id
        if not triggered_id:
            return dash.no_update

        clicked_index = triggered_id['index']
        if clicked_index == current_value:
            return None
        else:
            return clicked_index


    # ----------------------- Visualization + Tools -----------------------

    # Generate pathway overlay visualization
    @app.callback(
        Output('input-image', 'children', allow_duplicate=True),
        Input('spot-cell-option', 'data'),
        Input('pathway-input-container', 'value'),
        prevent_initial_call='initial_duplicate'
    )
    def callback_get_pathway_output(spot_cell_option, pathway_value):
        return get_pathway_output(spot_cell_option, pathway_value, folder_id, work_dir)

    # Generate gene overlay visualization
    @app.callback(
        Output('input-image', 'children', allow_duplicate=True),
        Input('spot-cell-option', 'data'),
        Input('gene-input-container', 'value'),
        prevent_initial_call='initial_duplicate'
    )
    def callback_get_gene(spot_cell_option, gene_chosen):
        print("gene_chosen", gene_chosen)
        print("spot_cell_option", spot_cell_option)
        return get_gene(spot_cell_option, gene_chosen, folder_id, work_dir)

    # Adjust vmin/vmax for cell gene expression
    @app.callback(
        Output('input-image', 'children', allow_duplicate=True),
        Input('cell-vminmax-button', 'n_clicks'),
        Input('cell-input-min', 'value'),
        Input('cell-input-max', 'value'),
        prevent_initial_call='initial_duplicate'
    )
    def callback_cell_vmin_vmax(n_clicks, vmin, vmax):
        if ctx.triggered_id == 'cell-vminmax-button':
            return cell_vmin_vmax(n_clicks, vmin, vmax, folder_id, work_dir)
        return dash.exceptions.PreventUpdate

    # Adjust vmin/vmax for spot gene expression
    @app.callback(
        Output('input-image', 'children', allow_duplicate=True),
        Input('spot-vminmax-button', 'n_clicks'),
        Input('spot-input-min', 'value'),
        Input('spot-input-max', 'value'),
        prevent_initial_call='initial_duplicate'
    )
    def callback_spot_vmin_vmax(n_clicks, vmin, vmax):
        if ctx.triggered_id == 'spot-vminmax-button':
            return spot_vmin_vmax(n_clicks, vmin, vmax, folder_id, work_dir)
        return dash.exceptions.PreventUpdate

    # Reset visualization and refresh rendering
    @app.callback(
        Output('input-image', 'children', allow_duplicate=True),
        Input("visual-input", "n_clicks"),
        Input('spot-cell-option', 'data'),
        Input('visual-type-container', 'data'),
        prevent_initial_call='initial_duplicate'
    )
    def callback_reset(n_clicks, spot_cell_option, visual_type):
        # Auto-switch to "CNV" (Cell Type) if Re-visualize is clicked and analysis results exist
        if ctx.triggered_id == "visual-input" and n_clicks:
             try:
                 # Check if cell type overlay exists
                 args = vio.load_json(f'{work_dir}/user{folder_id}/args.json')
                 if args.get("tutorialImagePath"):
                     print("DEBUG: Tutorial image is active. Using mock CNV overlay for layer controls.")
                     visual_type = "CNV"
                     spot_cell_option = "Cell data"
                     return reset(n_clicks, spot_cell_option, visual_type, folder_id, work_dir)

                 sample_id_file = args.get('sampleIdFile')
                 overlay_candidates = []
                 if sample_id_file:
                     overlay_candidates.append(f"{work_dir}/db/cache/{sample_id_file}-gis-blend-cell-type-img.tiff")
                 
                 existing_overlay = next((path for path in overlay_candidates if vio.exists(path)), None)
                 print(f"DEBUG: Loki overlay candidates: {overlay_candidates}; found={existing_overlay}")
                 if existing_overlay:
                     print("Loki results detected! Switching to CNV overlay view.")
                     visual_type = "CNV"
                     # Also ensure we are in "Cell data" mode?
                     spot_cell_option = "Cell data" 
                 elif visual_type == "CNV":
                     print("DEBUG: CNV overlay not found. Showing base image instead.", flush=True)
                     visual_type = None
             except Exception as e:
                 print(f"Error checking for Loki results: {e}")
                 
        return reset(n_clicks, spot_cell_option, visual_type, folder_id, work_dir)


    # ----------------------- Save & Export -----------------------

    # Trigger download of saved data
    @app.callback(
        Output('download', 'data'),
        Input('btn_save', 'n_clicks'),
        prevent_initial_call=True
    )
    def callback_copy_and_rename_file(n_clicks):
        return copy_and_rename_file(n_clicks, folder_id, work_dir, zip=True)

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
            result = get_roi_high_expression_genes(work_dir, coords, folder_id=folder_id, top_n=5)
            if not result:
                return status, html.Div(className="roi-gene-card", children=[
                    html.Div("ROI marker genes", className="roi-gene-title"),
                    html.Div("Upload a spatial .h5ad file to see enriched genes.", className="roi-gene-empty"),
                ])
            if result.get("selected_spots", 0) == 0:
                return status, html.Div(className="roi-gene-card", children=[
                    html.Div("ROI marker genes", className="roi-gene-title"),
                    html.Div("No spatial spots overlap this ROI.", className="roi-gene-empty"),
                ])

            gene_rows = []
            for gene in result.get("top_genes", [])[:5]:
                gene_rows.append(html.Div(className="roi-gene-row", children=[
                    html.Span(gene["gene"], className="roi-gene-name"),
                    html.Span(f"log2FC {gene['log2_fold_change']:.2g}", className="roi-gene-score"),
                ]))

            return status, html.Div(className="roi-gene-card", children=[
                html.Div(className="roi-gene-header", children=[
                    html.Div("ROI marker genes", className="roi-gene-title"),
                    html.Div(f"{result['selected_spots']} spots", className="roi-gene-count"),
                ]),
                html.Div(gene_rows, className="roi-gene-list"),
            ])
        except Exception as e:
            print(f"ROI gene popup error: {e}")
            return status, html.Div(className="roi-gene-card", children=[
                html.Div("ROI marker genes", className="roi-gene-title"),
                html.Div("Could not calculate ROI genes.", className="roi-gene-empty"),
            ])

    # (show_mouse_position removed - VivViewer does not emit clickData)

    # Run similar-cell search and output result
    @app.callback(
        Output('status8', 'data'),
        Input('btn_find', 'n_clicks'),
        prevent_initial_call='initial_duplicate'
    )
    def callbackcell_selection_interface(n_clicks):
        return cell_selection_interface(n_clicks, folder_id, work_dir)


    timer = threading.Timer(7200, clear_cache_forcall, args=(VALID_TOKEN, work_dir))
    timer.daemon = True
    timer.start()

    # Clear temp data and exit app
    @app.callback(
        Output('status6', 'children'),
        Output('start-loki-analysis-btn', 'disabled', allow_duplicate=True),
        Input('clear-cache', 'n_clicks'),
        prevent_initial_call=True
    )
    def callback_clear_cache_forcall(n_clicks):
        print(f"DEBUG: Clear Cache Clicked. n_clicks={n_clicks}")
        
        if n_clicks > 0:
            print("DEBUG: Executing clear_cache_forcall...")
            # We don't check for existence anymore, we just force exit
            clear_cache_forcall(VALID_TOKEN, work_dir)
            return None, True
        return dash.no_update, dash.no_update

    @app.callback(
        Output("input-image", "children", allow_duplicate=True),
        Output("loki-interval", "disabled"),
        Output("loki-progress-container", "style"),
        Output("cancel-loki-btn", "disabled", allow_duplicate=True),
        Output("cancel-loki-btn", "style", allow_duplicate=True),
        Input("start-loki-analysis-btn", "n_clicks"),
        prevent_initial_call=True
    )
    def start_loki_analysis(n_clicks):
        if n_clicks:
            print("Start Loki analysis clicked - Triggering Chat Job")
            
            # Use the token as session_id (same as chat api)
            session_id = args.token 

            # CLEANUP: Remove any stale cancel signal from previous runs
            try:
                cancel_path = os.path.join(CHAT_DIR, session_id, "cancel_signal")
                if os.path.exists(cancel_path):
                    os.remove(cancel_path)
                    print(f"DEBUG: Cleaned up stale cancel_signal for {session_id}")
            except: pass
            
            # Construct the prompt
            prompt = "run loki analysis"
            
            # We don't attach new images here, we assume the context is already set via enqueue_chat_job
            # But we should probably check if an image is loaded in work_dir to be safe?
            # actually chat_api handles looking up the image from work_dir.
            # So we just need to enqueue the message.
            
            try:
                # Reuse the logic from chat_api's enqueue
                # But we need to call enqueue_chat_job which is def enqueue_chat_job(session_id, model, prompt, images, work_dir, roi_path=None)
                
                # Retrieve Work Dir & Image Path (similar to chat_api)
                # Image is in work_dir/db/data/{sample_id}-wsi-img.tiff
                try:
                    args_json = vio.load_json(f'{work_dir}/user/args.json')
                    sample_id = args_json.get('sampleId', 'default')
                    sample_id_file = args_json.get('sampleIdFile')
                except:
                    args_json = {}
                    sample_id = "default"
                    sample_id_file = None
                

                image_s3_path = args_json.get("tutorialImagePath") or f"{work_dir}/db/data/{sample_id}-wsi-img.tiff"
                images = []
                if vio.exists(image_s3_path):
                     images.append(image_s3_path)
                print(f"DEBUG: Start Loki sample_id={sample_id}, sample_id_file={sample_id_file}, image={image_s3_path}, exists={bool(images)}")

                if sample_id_file:
                    overlay_s3_path = f"{work_dir}/db/cache/{sample_id_file}-gis-blend-cell-type-img.tiff"
                    try:
                        if vio.exists(overlay_s3_path):
                            vio.remove(overlay_s3_path)
                            print(f"DEBUG: Removed stale Loki overlay before new run: {overlay_s3_path}")
                    except Exception as e:
                        print(f"Warning: Failed to remove stale Loki overlay {overlay_s3_path}: {e}")
                
                # Enqueue the job
                status = enqueue_chat_job(
                    session_id=session_id,
                    model="qwen2.5vl:72b", # Default model
                    prompt=prompt,
                    images=images,
                    work_dir=work_dir,
                    roi_path=None, # No specific ROI from button click yet
                    visible=False  # HIDE from Chat UI
                )
                
                # --- FIX: Reset Status to avoid "100%" flash from previous run ---
                try:
                    reset_status = {
                        "percent": 0,
                        "message": "Initializing...",
                        "status": "queued",
                        "timestamp": time.time()
                    }
                    status_dir = os.path.join(CHAT_DIR, session_id)
                    os.makedirs(status_dir, exist_ok=True)
                    status_path = os.path.join(status_dir, "loki_status.json")
                    with open(status_path, 'w') as sf:
                        json.dump(reset_status, sf)
                    print(f"DEBUG: Reset loki_status.json for {session_id}")
                except Exception as e:
                    print(f"Error resetting status: {e}")
                # -------------------------------------------------------------
                
                # Enable Cancel Button (False = Enabled) AND make it visible (display: block)
                # Ensure the progress bar becomes visible immediately
                # Return dash.no_update to keep the current image/content visible
                return dash.no_update, False, {'display': 'block', 'marginTop': '15px'}, False, {'width': '35%', 'marginTop': '10px', 'marginLeft': 'auto', 'marginRight': 'auto', 'textAlign': 'center', 'display': 'block'}
                return dash.no_update, False, {'display': 'block', 'marginTop': '15px'}
                
            except Exception as e:
                print(f"Error starting analysis: {e}")
                return html.Div([
                    html.Br(), html.H3(f"Error: {e}", style={'color': 'red', 'textAlign': 'center'})
                ]), True, {'display': 'none'}, True, {'display': 'none'}
                
        return dash.no_update, dash.no_update, dash.no_update

    # Restore progress bar state on page reload
    @app.callback(
        Output("loki-interval", "disabled", allow_duplicate=True),
        Output("loki-progress-container", "style", allow_duplicate=True),
        Output("cancel-loki-btn", "style", allow_duplicate=True),
        Input("url", "pathname"),
        prevent_initial_call="initial_duplicate"
    )
    def restore_progress_state(pathname):
        # Always hide progress bar on page load
        # It should only appear when the user clicks "Start Loki Analysis"
        return True, {'display': 'none'}, {'display': 'none'}


    @app.callback(
        Output("loki-progress-bar", "style"),
        Output("loki-progress-percent", "children"),
        Output("loki-progress-label", "children"),
        Output("loki-interval", "disabled", allow_duplicate=True),
        Output("cancel-loki-btn", "style", allow_duplicate=True),
        Input("loki-interval", "n_intervals"),
        prevent_initial_call=True
    )
    def update_loki_progress(n):
        session_id = args.token
        status_path = os.path.join(CHAT_DIR, session_id, "loki_status.json")

        try:
            if not os.path.exists(status_path):
                return {'width': '0%'}, "0%", "Waiting for worker...", False, dash.no_update

            with open(status_path) as sf:
                status_data = json.load(sf)

            percent = status_data.get("percent", 0)
            message = status_data.get("message", "Processing...")
            status = status_data.get("status", "processing")

            bar_style = {'width': f'{percent}%', 'height': '100%', 'backgroundColor': '#0071e3', 'transition': 'width 0.5s ease'}

            if status == "done" or percent >= 100:
                return bar_style, "100%", "Complete", True, {'display': 'none'}

            return bar_style, f"{percent}%", message, False, {'width': '35%', 'marginTop': '10px', 'marginLeft': 'auto', 'marginRight': 'auto', 'textAlign': 'center', 'display': 'block'}

        except Exception as e:
            print(f"Error poll loki status: {e}")
            return {'width': '0%', 'backgroundColor': 'red'}, "Error", "Connection Error", True, {'display': 'none'}

    # --- CANCEL LOKI ANALYSIS ---
    @app.callback(
        Output("loki-interval", "disabled", allow_duplicate=True),
        Output("loki-progress-label", "children", allow_duplicate=True),
        Output("loki-progress-bar", "style", allow_duplicate=True),
        Output("cancel-loki-btn", "disabled"),
        Output("start-loki-analysis-btn", "disabled", allow_duplicate=True),
        Output("cancel-loki-btn", "style", allow_duplicate=True),
        Input("cancel-loki-btn", "n_clicks"),
        State("loki-interval", "disabled"),
        prevent_initial_call=True
    )
    def cancel_loki_analysis(n_clicks, interval_disabled):
        if not n_clicks:
            return dash.no_update
        
        print("DEBUG: Cancel Loki Analysis Triggered.")
        session_id = args.token
        
        # 1. Write Cancel Signal File (Robust Semaphore)
        try:
            cancel_dir = os.path.join(CHAT_DIR, session_id)
            os.makedirs(cancel_dir, exist_ok=True)
            with open(os.path.join(cancel_dir, "cancel_signal"), 'w') as csf:
                csf.write("")
            print(f"DEBUG: Wrote cancel_signal for {session_id}")
        except Exception as e:
            print(f"DEBUG: Failed to write cancel signal: {e}")

        # 2. Update Status to Cancelled (Visual Only)
        try:
            cancel_status = {
                "percent": 0,
                "message": "Cancelled by user.",
                "status": "cancelled",
                "timestamp": time.time()
            }
            cancel_dir = os.path.join(CHAT_DIR, session_id)
            os.makedirs(cancel_dir, exist_ok=True)
            with open(os.path.join(cancel_dir, "loki_status.json"), 'w') as sf:
                json.dump(cancel_status, sf)
        except: pass

        # 3. Disable Interval, Update UI
        bar_style = {'width': '0%', 'backgroundColor': '#ff3b30'}
        # Keep cancel disabled after clicking, re-enable start
        # AND HIDE CANCEL BUTTON
        return True, "Cancelled", bar_style, True, False, {'display': 'none'}


    # --- OLLAMA WARMUP ---
    def warmup_ollama():
        print("DEBUG: Sending Warmup 'hi' to Ollama...")
        try:
             enqueue_chat_job(
                session_id=VALID_TOKEN,
                model="qwen2.5vl:72b",
                prompt="hi",
                images=[],
                work_dir=work_dir,
                roi_path=None,
                visible=False # Invisible to user
            )
             print("DEBUG: Warmup 'hi' sent successfully.")
        except Exception as e:
            print(f"DEBUG: Warmup failed: {e}")

    # Start Warmup in background thread so it doesn't block app launch
    threading.Thread(target=warmup_ollama, daemon=True).start()

    # Launch browser and run app
    # threading.Thread(target=open_browser, args=(HOST, args.port, args.token)).start()
    app.run_server(host=HOST, port=args.port, debug=False, dev_tools_hot_reload=True)


    

if __name__ == "__main__":
    main()
