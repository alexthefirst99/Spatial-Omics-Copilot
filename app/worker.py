import os
import time
import shutil
import tempfile
import copy
import threading
import concurrent.futures

try:
    from app.session import (
        CHAT_DIR, _read_session, _write_session, _session_path,
        _lock_and_read_session, _lock_and_write_session,
        safe_update_session, safe_update_streaming_message,
        safe_update_last_assistant_image,
    )
    from app.inference import run_model_inference
    from app.image_utils import crop_image_by_roi
except ImportError:
    from session import (
        CHAT_DIR, _read_session, _write_session, _session_path,
        _lock_and_read_session, _lock_and_write_session,
        safe_update_session, safe_update_streaming_message,
        safe_update_last_assistant_image,
    )
    from inference import run_model_inference
    from image_utils import crop_image_by_roi

_bot_executor = concurrent.futures.ThreadPoolExecutor(max_workers=50)
processing_keys = set()
processing_lock = threading.Lock()


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
                rag_context_str = last_msg.get("rag_context_str", "")
                if rag_context_str and inference_messages:
                    inference_messages[-1]["content"] = inference_messages[-1].get("content", "") + rag_context_str
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


def enqueue_chat_job(session_id, model, prompt, images, work_dir, roi_path=None, visible=True, rag_context_str=""):
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
    if rag_context_str:
        new_message["rag_context_str"] = rag_context_str

    session_data["messages"].append(new_message)
    session_data["updated_at"] = time.time()

    _lock_and_write_session(session_file, session_data)

    # Dispatch directly — no separate polling loop needed
    with processing_lock:
        if session_id not in processing_keys:
            processing_keys.add(session_id)
            _bot_executor.submit(process_session, session_id)

    return "queued"
