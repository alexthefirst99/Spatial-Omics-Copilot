import os
import json
import time
import shutil
import urllib.parse
from flask import request, jsonify, send_file, redirect
import cv2
import numpy as np

try:
    from app.session import (
        CHAT_DIR, _session_path, _lock_and_read_session,
    )
    from app.worker import enqueue_chat_job
    from app.image_utils import TMP_BASE, ensure_ome_tiff_cached
    import niceview.utils.io as vio
except ImportError:
    from session import (
        CHAT_DIR, _session_path, _lock_and_read_session,
    )
    from worker import enqueue_chat_job
    from image_utils import TMP_BASE, ensure_ome_tiff_cached
    import niceview.utils.io as vio

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

TUTORIAL_IMAGE_PATH = os.environ.get(
    'COPILOT_TUTORIAL_IMAGE',
    os.path.join(_PROJECT_ROOT, 'tutorial', 'loki_tutorial_hskin_melanoma_downsampled.ome.tif')
)
TUTORIAL_SAMPLE_ID = "copilot-tutorial"
TUTORIAL_SAMPLE_ID_FILE = "copilot-tutorial-file-name"

try:
    from rag.agent import run_agent
except ImportError:
    run_agent = None


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
            prompt += "\n\nRespond in 3–4 sentences maximum. Be direct and concise."

            # RAG pipeline — reads cached gene_objects written by popup callbacks
            rag_metadata = None
            try:
                if run_agent:
                    _cluster_path = f'{work_dir}/user/cluster_context.json'
                    _roi_path = f'{work_dir}/user/roi_context.json'

                    _user_message = data.get("prompt", "")
                    if vio.exists(_cluster_path):
                        _cctx = vio.load_json(_cluster_path)
                        _rag = run_agent(work_dir, message=_user_message, cluster_id=_cctx.get("cluster_id"))
                    elif vio.exists(_roi_path):
                        _rctx = vio.load_json(_roi_path)
                        _coords = vio.load_json(f'{work_dir}/user/coords.json') if vio.exists(f'{work_dir}/user/coords.json') else None
                        _rag = run_agent(work_dir, message=_user_message, coords=_coords)
                    else:
                        _rag = run_agent(work_dir, message=_user_message)  # demo fallback

                    rag_context_str = _rag["context_str"]
                    rag_metadata = _rag["metadata"]
                    print(f"DEBUG: RAG ran for {rag_metadata['label']}")
            except Exception as e:
                print(f"DEBUG: RAG pipeline failed: {e}")

            status = enqueue_chat_job(
                session_id=session_id,
                model=data.get("model", "qwen2.5vl:72b"),
                prompt=prompt,
                images=images,
                work_dir=work_dir,
                roi_path=roi_s3_path,
                rag_context_str=rag_context_str if 'rag_context_str' in locals() else "",
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
                "images": images,
                "roi_image": preview_img,
                "rag_metadata": rag_metadata,
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
