import os
import json
import time
import shutil
import urllib.parse
from flask import request, jsonify, send_file, redirect
import cv2
import numpy as np

from app.config import get_path
from app.roi_context import ensure_roi_context

try:
    from app.session import (
        CHAT_DIR, _session_path, _lock_and_read_session, _lock_and_write_session,
        build_rag_record, finalize_rag_record,
    )
    from app.worker import enqueue_chat_job, ensure_session_processing
    from app.image_utils import TMP_BASE, ensure_ome_tiff_cached, resolve_active_image_path
    import niceview.utils.io as vio
except ImportError:
    from session import (
        CHAT_DIR, _session_path, _lock_and_read_session, _lock_and_write_session,
        build_rag_record, finalize_rag_record,
    )
    from worker import enqueue_chat_job, ensure_session_processing
    from image_utils import TMP_BASE, ensure_ome_tiff_cached, resolve_active_image_path
    import niceview.utils.io as vio

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

TUTORIAL_IMAGE_PATH = get_path(
    'paths.tutorial_image',
    os.path.join(_PROJECT_ROOT, 'tutorial', 'loki_tutorial_hskin_melanoma_downsampled.ome.tif'),
    env='COPILOT_TUTORIAL_IMAGE',
)
TUTORIAL_SAMPLE_ID = "copilot-tutorial"
TUTORIAL_SAMPLE_ID_FILE = "copilot-tutorial-file-name"


def _chat_stream_timeout_seconds():
    try:
        return int(os.environ.get("COPILOT_CHAT_STREAM_TIMEOUT") or os.environ.get("OLLAMA_TIMEOUT") or 120)
    except (TypeError, ValueError):
        return 120

try:
    from rag.agent import run_agent, run_copilot_agent
except ImportError:
    run_agent = None
    run_copilot_agent = None

try:
    from app.inference import get_default_model_spec, run_model_inference
except ImportError:
    from inference import get_default_model_spec, run_model_inference

def _disease_cache_path(work_dir, folder_id=""):
    return f"{work_dir}/user{folder_id}/disease_context.json"


def _extract_disease_context(work_dir, session_id, current_message, folder_id=""):
    """Extract and cache disease or tissue context from the conversation."""

    cache_path = _disease_cache_path(work_dir, folder_id)
    try:
        if vio.exists(cache_path):
            cached = vio.load_json(cache_path).get("disease")
            if cached:
                return cached
    except Exception as e:
        print(f"DEBUG: Disease context cache read failed: {e}")

    try:
        session_data = _lock_and_read_session(_session_path(session_id)) or {}
        prior = [
            m for m in session_data.get("messages", [])
            if m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
            and not m.get("streaming") and m.get("content") != "..."
        ]
        convo_lines = [f"{m['role']}: {m['content'].strip()}" for m in prior[-20:]]
        if current_message:
            convo_lines.append(f"user: {current_message.strip()}")
        if not convo_lines:
            return None

        extraction_prompt = (
            "Below is a conversation between a researcher and an assistant "
            "about a spatial transcriptomics tissue sample.\n\n"
            + "\n".join(convo_lines)
            + "\n\nHas the researcher stated what disease or tissue type this "
            "sample is from? Reply with ONLY the disease/tissue name in a few "
            "words (e.g. \"colorectal cancer\"). If it was never stated, "
            "reply with exactly: unknown"
        )
        model_spec = get_default_model_spec()
        provider, model_name = model_spec.split(":", 1)
        response = "".join(
            run_model_inference(
                [{"role": "user", "content": extraction_prompt}],
                provider=provider,
                model_name=model_name or None,
            )
        ).strip()

        cleaned = response.strip().strip(".").strip()
        error_prefixes = ("deepinfra ", "ollama ", "error ", "unsupported ")
        if (
            not cleaned
            or "unknown" in cleaned.lower()
            or "not configured" in cleaned.lower()
            or cleaned.lower().startswith(error_prefixes)
            or len(cleaned) > 80
        ):
            return None

        try:
            vio.dump_json({"disease": cleaned}, cache_path)
        except Exception as e:
            print(f"DEBUG: Disease context cache write failed: {e}")
        return cleaned
    except Exception as e:
        print(f"DEBUG: Disease context extraction failed: {e}")
        return None


def register_chat_routes(server, workspace_id, work_dir, base_path=None):
    base_path = base_path or f"/workspaces/{workspace_id}"

    # These routes must precede Dash's wildcard routes.

    @server.route(f"{base_path}/chat", methods=["POST"])
    def chat_api():
        print(f"DEBUG: chat_api called for workspace={workspace_id}")
        try:
            data = request.get_json(force=True)
            session_id = workspace_id

            images = []
            roi_s3_path = None

            try:
                is_duplicate = False
                last_roi_s3_key_file = None
                args = vio.load_json(f'{work_dir}/user/args.json')

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
                    # visualizeOption remains a text mode used by older callers.
                    args['activeLayer'] = active_layer_index
                    vio.dump_json(args, f'{work_dir}/user/args.json')

                visual_option = args.get('visualizeOption', 'Original')

                requested_layer = active_layer_index
                if requested_layer is None and (
                    "Cell Type" in visual_option or visual_option == "CNV"
                ):
                    requested_layer = 1

                image_s3_path, resolved_layer_index, _image_layers = resolve_active_image_path(
                    work_dir, args, requested_layer
                )
                layer_labels = args.get("imageLayerLabels")
                if isinstance(layer_labels, list) and resolved_layer_index < len(layer_labels):
                    active_layer_label = layer_labels[resolved_layer_index]
                else:
                    active_layer_label = "Original" if resolved_layer_index == 0 else os.path.basename(image_s3_path)
                print(
                    f"DEBUG: Active visual layer: {resolved_layer_index} "
                    f"({active_layer_label})"
                )
                if requested_layer is not None and resolved_layer_index != requested_layer:
                    print(
                        f"DEBUG: Active layer {requested_layer} is unavailable; "
                        "using image layer 0."
                    )
                elif resolved_layer_index > 0:
                    print(
                        f"DEBUG: Using active layer {resolved_layer_index} for chat crop: "
                        f"{image_s3_path}"
                    )

                # Reuse a selection-time crop only when it belongs to this image.
                selection_time_crop = None
                _crop_cache_path = f"{work_dir}/user/roi_crop.png"
                _crop_meta_path = f"{work_dir}/user/roi_crop_meta.json"
                if vio.exists(_crop_cache_path) and vio.exists(_crop_meta_path):
                    try:
                        if vio.load_json(_crop_meta_path).get("image_path") == image_s3_path:
                            selection_time_crop = _crop_cache_path
                    except Exception as e_cache:
                        print(f"DEBUG: Failed to read ROI crop cache: {e_cache}")
                if selection_time_crop:
                    print(f"DEBUG: Reusing selection-time ROI crop: {selection_time_crop}")
                    images = [selection_time_crop]
                    preview_crop_path = selection_time_crop
                    roi_s3_path = None

                # Prefer pixel coordinates written by save_roi().
                user_coords_path = f"{work_dir}/user/coords.json"
                user_roi_path = f"{work_dir}/user/roi.json"

                roi_json_to_upload = None

                if not selection_time_crop and vio.exists(user_coords_path):
                    try:
                        with vio.open_file(user_coords_path, "r") as f_coords:
                            coords_data = json.load(f_coords)
                            if coords_data:
                                roi_json_to_upload = coords_data
                                print(f"DEBUG: Using coords.json (pixels) for ROI. Num polygons: {len(coords_data)}")

                                roi_s3_path = None

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

                                            if vio.exists(last_roi_s3_key_file):
                                                 with vio.open_file(last_roi_s3_key_file, 'r') as f:
                                                     roi_s3_path = f.read().strip()

                                            if vio.exists(last_crop_path_file):
                                                 with vio.open_file(last_crop_path_file, 'r') as f:
                                                     preview_crop_path = f.read().strip()
                                                     print(f"DEBUG: Reusing Crop Path: {preview_crop_path}")

                                            if not roi_s3_path:
                                                print("DEBUG: Cache missing S3 key. Forcing re-upload.")
                                                is_duplicate = False
                                    except Exception as e:
                                        print(f"DEBUG: Cache check failed: {e}")
                                try:
                                    import tifffile as tf
                                    import numpy as np
                                    import cv2

                                    all_points = []
                                    for poly in coords_data:
                                        all_points.extend(poly)

                                    if all_points and vio.exists(image_s3_path) and not is_duplicate:
                                        pts = np.array(all_points)
                                        x_min, y_min = np.min(pts, axis=0)
                                        x_max, y_max = np.max(pts, axis=0)

                                        pad = 0
                                        x_min = max(0, int(x_min) - pad)
                                        y_min = max(0, int(y_min) - pad)
                                        x_max = int(x_max) + pad
                                        y_max = int(y_max) + pad

                                        print(f"DEBUG: Cropping ROI: [{x_min}:{x_max}, {y_min}:{y_max}] from {image_s3_path}")

                                        with vio.open_file(image_s3_path, "rb") as f_img:
                                            crop = None
                                            try:
                                                import tifffile as tf
                                                with tf.TiffFile(f_img) as tif:
                                                    page = tif.pages[0]
                                                    ih, iw = page.shape[0], page.shape[1]
                                                    x_max = min(x_max, iw)
                                                    y_max = min(y_max, ih)

                                                    if x_max > x_min and y_max > y_min:
                                                        crop = page.asarray()[y_min:y_max, x_min:x_max]
                                            except Exception as e_tif:
                                                print(f"DEBUG: Not a TIFF or Tifffile failed: {e_tif}. Trying PIL.")
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
                                                     crop = cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)

                                                vio.write_image(crop_path, crop)

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

                # Fall back to geographic ROI data when pixel coordinates are unavailable.
                if 'preview_crop_path' not in locals():
                     preview_crop_path = None

                if not selection_time_crop and not roi_json_to_upload and vio.exists(user_roi_path):
                    try:
                        with vio.open_file(user_roi_path, "r") as f_src:
                            roi_json = json.load(f_src)
                            if ("features" in roi_json and roi_json["features"]) or "geometry" in roi_json:
                                roi_json_to_upload = roi_json
                                print(f"DEBUG: Using original roi.json (Lat/Lon)")

                                coords_path = f"{work_dir}/user/coords.json"

                                if vio.exists(coords_path) and vio.exists(image_s3_path):
                                    try:
                                        import tifffile as tf
                                        import numpy as np
                                        import cv2

                                        coords_data = vio.load_json(coords_path)
                                        all_points = []
                                        for poly in coords_data:
                                            all_points.extend(poly)

                                        if all_points:
                                            pts = np.array(all_points)
                                            x_min, y_min = np.min(pts, axis=0)
                                            x_max, y_max = np.max(pts, axis=0)

                                            pad = 0
                                            x_min = max(0, int(x_min) - pad)
                                            y_min = max(0, int(y_min) - pad)
                                            x_max = int(x_max) + pad
                                            y_max = int(y_max) + pad

                                            with vio.open_file(image_s3_path, "rb") as f_img:
                                                crop = None
                                                try:
                                                    import tifffile as tf
                                                    with tf.TiffFile(f_img) as tif:
                                                        page = tif.pages[0]
                                                        ih, iw = page.shape[0], page.shape[1]
                                                        x_max = min(x_max, iw)
                                                        y_max = min(y_max, ih)

                                                        if x_max > x_min and y_max > y_min:
                                                            crop = page.asarray()[y_min:y_max, x_min:x_max]
                                                except Exception as e_tif:
                                                    print(f"DEBUG: Fallback Not a TIFF or Tifffile failed: {e_tif}. Trying PIL.")
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

                last_roi_path = f"{work_dir}/user/last_processed_roi.json"
                last_img_path_files = f"{work_dir}/user/last_processed_image.txt"

                send_image = False
                send_roi = False

                if roi_json_to_upload:
                    send_image = True
                    send_roi = True

                    if vio.exists(last_roi_path) and vio.exists(last_img_path_files):
                        try:
                            last_roi_data = vio.load_json(last_roi_path)
                            with vio.open_file(last_img_path_files, 'r') as f_img:
                                last_img_path = f_img.read().strip()

                            pass
                        except Exception as e:
                            print(f"DEBUG: Optimization check failed: {e}")

                    if send_roi or send_image:
                         vio.dump_json(roi_json_to_upload, last_roi_path)
                         with vio.open_file(last_img_path_files, 'w') as f_img:
                             f_img.write(image_s3_path)

                elif not selection_time_crop:
                    # Reset duplicate detection after the ROI is cleared.
                    if vio.exists(last_roi_path): vio.remove(last_roi_path)
                    if vio.exists(last_img_path_files): vio.remove(last_img_path_files)
                    print("DEBUG: No ROI selected. Sending text only (No Image).")
                else:
                    print("DEBUG: ROI selected. Using cached selection-time crop.")

                # Avoid a remote existence check for S3 paths.
                if send_image and (image_s3_path and (image_s3_path.startswith("s3://") or vio.exists(image_s3_path))):
                     images.append(image_s3_path)
                     print(f"DEBUG: Attaching Image: {image_s3_path}")

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
                            if last_roi_s3_key_file:
                                try:
                                    with vio.open_file(last_roi_s3_key_file, 'w') as f:
                                        f.write(roi_local_path)
                                except: pass
                        except Exception as e:
                             print(f"DEBUG: Failed to save ROI: {e}")

                else:
                    if selection_time_crop:
                         print("DEBUG: Using cached ROI crop as image context.")
                    elif not images:
                         print("DEBUG: No new ROI/Image sent (Optimization active).")
                    else:
                         print("DEBUG: Image context present without ROI coordinates.")

            except Exception as e:
                print(f"ERROR handling ROI/Image: {e}")

            prompt = data.get("prompt", "")
            prompt += "\n\nRespond in 1-2 concise sentences. Be direct."

            # Popup callbacks cache the gene objects used here.
            rag_record = None
            try:
                if run_agent:
                    _cluster_path = f'{work_dir}/user/cluster_context.json'
                    _roi_path = f'{work_dir}/user/roi_context.json'

                    _user_message = data.get("prompt", "")
                    _disease = _extract_disease_context(work_dir, session_id, _user_message)

                    def _run_rag(gene_objects, label):
                        if run_copilot_agent:
                            return run_copilot_agent(
                                question=_user_message,
                                deg=gene_objects,
                                label=label,
                                disease=_disease,
                            ).to_legacy_dict()
                        return run_agent(gene_objects, message=_user_message, label=label)

                    # Both context files persist, so use the most recently updated one.
                    _cluster_exists = vio.exists(_cluster_path)
                    _roi_exists = vio.exists(_roi_path)

                    # ``coords.json`` is published before Dash finishes the
                    # expensive DEG calculation.  Synchronize here so the
                    # first chat turn cannot race ahead with image-only
                    # context (and never reuse genes from a previous ROI).
                    _coords_path = f'{work_dir}/user/coords.json'
                    if vio.exists(_coords_path):
                        try:
                            _coords = vio.load_json(_coords_path)
                            if _coords:
                                ensure_roi_context(work_dir, _coords, top_n=25)
                                _roi_exists = vio.exists(_roi_path)
                        except Exception as _roi_context_error:
                            print(
                                "DEBUG: Could not synchronize ROI gene context: "
                                f"{_roi_context_error}"
                            )

                    _use_cluster = _cluster_exists and (
                        not _roi_exists
                        or os.path.getmtime(_cluster_path) >= os.path.getmtime(_roi_path)
                    )

                    if _use_cluster:
                        _cctx = vio.load_json(_cluster_path)
                        _gene_objects = _cctx.get("gene_objects", [])
                        _label = f"Cluster {_cctx.get('cluster_id', '?')}"
                        _rag = _run_rag(_gene_objects, _label)
                    elif _roi_exists:
                        _rctx = vio.load_json(_roi_path)
                        _gene_objects = _rctx.get("gene_objects", [])
                        _rag = _run_rag(_gene_objects, "ROI") if _gene_objects else None
                    else:
                        _rag = None

                    if _rag:
                        rag_context_str = _rag["context_str"]
                        _agent_metadata = _rag["metadata"]
                        print(f"DEBUG: RAG ran for {_agent_metadata['label']}")

                        # Generation happens outside the agent graph. Persist it as
                        # a dedicated workflow block instead of appending a synthetic
                        # trace step. This keeps session.json readable and avoids any
                        # persisted ``*.trace`` field.
                        _synth_model = data.get("model") or get_default_model_spec()
                        rag_record = build_rag_record(
                            _agent_metadata,
                            generation_model=_synth_model,
                            context_chars=len(rag_context_str),
                        )
            except Exception as e:
                print(f"DEBUG: RAG pipeline failed: {e}")

            status = enqueue_chat_job(
                session_id=session_id,
                model=data.get("model") or get_default_model_spec(),
                prompt=prompt,
                images=images,
                work_dir=work_dir,
                roi_path=roi_s3_path,
                rag_context_str=rag_context_str if 'rag_context_str' in locals() else "",
                rag=rag_record,
            )
            if status == "busy":
                return jsonify({
                    "status": "error",
                    "message": "Still processing the previous chat message. Wait for it to finish or clear the session."
                })

            if 'preview_crop_path' in locals() and preview_crop_path:
                 preview_img = preview_crop_path
            else:
                 preview_img = None

            return jsonify({
                "status": status,
                "images": images,
                "roi_image": preview_img,
                "rag": rag_record,
            })
        except Exception as e:
            print(f"ERROR in chat_api: {e}")
            return jsonify({"status": "error", "message": str(e)})

    @server.route(f"{base_path}/chat/poll", methods=["GET"])
    def chat_poll_api():
        try:
            session_id = workspace_id
            session_file = _session_path(session_id)
            try:
                current_data = _lock_and_read_session(session_file)
                if current_data:
                    messages = current_data.get("messages", [])
                    if not messages:
                        return jsonify({"status": "idle"})
                    if messages:
                        last_msg = messages[-1]
                        if last_msg.get("role") == "assistant":
                            is_streaming = last_msg.get("streaming", False)
                            if is_streaming:
                                started_at = float(last_msg.get("timestamp") or time.time())
                                if time.time() - started_at > _chat_stream_timeout_seconds():
                                    timeout_message = (
                                        "Chat generation timed out. Try a smaller ROI, or restart Ollama if it is still busy."
                                    )
                                    last_msg["streaming"] = False
                                    if not last_msg.get("content") or last_msg.get("content") == "...":
                                        last_msg["content"] = timeout_message
                                    last_msg["rag"] = finalize_rag_record(
                                        last_msg.get("rag") or last_msg.get("rag_metadata"),
                                        success=False,
                                        error=timeout_message,
                                    )
                                    current_data["updated_at"] = time.time()
                                    _lock_and_write_session(session_file, current_data)
                                    is_streaming = False
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
                                "visible": last_msg.get("visible", True),
                                "rag": build_rag_record(last_msg.get("rag") or last_msg.get("rag_metadata")),
                            })
                        if last_msg.get("role") == "user":
                            ensure_session_processing(session_id)
            except Exception as e:
                print(f"Poll check error: {e}")
                return jsonify({"status": "error", "message": str(e)})
            return jsonify({"status": "processing"})
        except Exception as e:
            return jsonify({"status": "error", "message": f"API Error: {str(e)}"})

    @server.route(f"{base_path}/chat/clear", methods=["POST"])
    def clear_session_api():
        try:
            session_id = workspace_id
            errors = []

            try:
                session_dir = os.path.join(CHAT_DIR, session_id)
                if os.path.isdir(session_dir):
                    shutil.rmtree(session_dir)
                    print(f"[chat/clear] Chat session dir deleted for {session_id}")
            except Exception as e:
                errors.append(f"chat session dir: {e}")

            try:
                user_cache_dir = os.path.join(TMP_BASE, "ome_tiff_cache", workspace_id)
                if os.path.exists(user_cache_dir):
                    shutil.rmtree(user_cache_dir)
                    print(f"[chat/clear] OME-TIFF cache wiped for {workspace_id}")
            except Exception as e:
                errors.append(f"OME-TIFF cache: {e}")

            try:
                user_state_dir = os.path.join(work_dir, "user")
                if os.path.isdir(user_state_dir):
                    for entry in os.listdir(user_state_dir):
                        entry_path = os.path.join(user_state_dir, entry)
                        if os.path.isfile(entry_path):
                            os.remove(entry_path)
                        elif os.path.isdir(entry_path):
                            shutil.rmtree(entry_path)
                    print(f"[chat/clear] work_dir/user/ wiped for {workspace_id}")
            except Exception as e:
                errors.append(f"work_dir/user/: {e}")

            try:
                tmp_upload_dir = os.path.join(work_dir, "data_input_temp", "tmp")
                if os.path.isdir(tmp_upload_dir):
                    shutil.rmtree(tmp_upload_dir)
                    os.makedirs(tmp_upload_dir, exist_ok=True)
                    print(f"[chat/clear] Upload tmp dir wiped for {workspace_id}")
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

            all_files.sort()  # Oldest first.
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

    @server.route(f"{base_path}/ome_tiff")
    def serve_ome_tiff():
        path = request.args.get("path")
        if not path:
            return "Missing path", 400
        path = urllib.parse.unquote(path)

        try:
            parent_cache_dir = os.path.join(TMP_BASE, "ome_tiff_cache")
            prune_ome_tiff_cache(parent_cache_dir, max_gb=10)
            ome_local_path = ensure_ome_tiff_cached(path, workspace_id)
            return send_file(ome_local_path, conditional=True, mimetype='image/tiff')
        except FileNotFoundError:
            return f"Not found: {path}", 404
        except Exception as e:
            print(f"OME-TIFF Serve Error: {e}")
            import traceback; traceback.print_exc()
            return f"Error reading file: {str(e)}", 500

    @server.route(f"{base_path}/preview")
    def preview_image():
        import cv2
        import numpy as np
        import io

        path = request.args.get("path")
        if not path:
            return "Missing path", 400
        path = urllib.parse.unquote(path)

        try:
            if path.startswith("https://") and ".s3." in path and "amazonaws.com" in path:
                try:
                    parts = path.split("amazonaws.com/")
                    if len(parts) > 1:
                        key = parts[1]
                        domain_parts = parts[0].split(".s3.")
                        if len(domain_parts) > 0:
                            bucket = domain_parts[0].replace("https://", "")
                            path = f"s3://{bucket}/{key}"
                            print(f"DEBUG: Converted HTTPS URL to s3:// URI: {path}")
                except Exception as e:
                    print(f"Warning: Failed to parse HTTPS S3 URL: {e}")

            if vio.exists(path):
                with vio.open_file(path, 'rb') as f:
                    file_content = f.read()

                nparr = np.frombuffer(file_content, np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

                if img is None:
                     return send_file(io.BytesIO(file_content), mimetype="application/octet-stream")

                h, w = img.shape[:2]
                max_dim = 512
                if h > max_dim or w > max_dim:
                    scale = max_dim / max(h, w)
                    new_w = int(w * scale)
                    new_h = int(h * scale)
                    img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_AREA)

                _, img_encoded = cv2.imencode('.png', img)
                return send_file(io.BytesIO(img_encoded.tobytes()), mimetype="image/png")
            else:
                return f"Not found: {path}", 404
        except Exception as e:
            print(f"Preview Error: {e}")
            return f"Error reading file: {str(e)}", 500
