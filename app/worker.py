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
        _lock_and_update_session,
        safe_update_session, safe_update_streaming_message,
        safe_update_last_assistant_image,
    )
    from app.inference import run_model_inference
    from app.image_utils import crop_image_by_roi
except ImportError:
    from session import (
        CHAT_DIR, _read_session, _write_session, _session_path,
        _lock_and_read_session, _lock_and_write_session,
        _lock_and_update_session,
        safe_update_session, safe_update_streaming_message,
        safe_update_last_assistant_image,
    )
    from inference import run_model_inference
    from image_utils import crop_image_by_roi

_bot_executor = concurrent.futures.ThreadPoolExecutor(max_workers=50)
processing_keys = set()
processing_lock = threading.Lock()


def _has_pending_user_message(session_id):
    data = _read_session(session_id)
    messages = data.get("messages", []) if data else []
    return bool(messages and messages[-1].get("role") == "user")


def ensure_session_processing(session_id):
    with processing_lock:
        if session_id in processing_keys:
            return False
        if not _has_pending_user_message(session_id):
            return False
        processing_keys.add(session_id)
        _bot_executor.submit(process_session, session_id)
        return True


def _update_assistant_message(session_id, target_timestamp, content, streaming=False):
    def updater(data):
        if not data:
            return None
        for msg in reversed(data.get("messages", [])):
            if msg.get("role") != "assistant":
                continue
            if abs(msg.get("timestamp", 0) - target_timestamp) > 1.0:
                continue
            msg["content"] = content
            if streaming:
                msg["streaming"] = True
            else:
                msg.pop("streaming", None)
            data["updated_at"] = time.time()
            return data
        return None

    return _lock_and_update_session(_session_path(session_id), updater)


def _build_inference_messages(messages, current_user_message):
    history = []
    for msg in messages:
        if msg is current_user_message:
            continue
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if msg.get("streaming") or content == "...":
            continue
        history.append({"role": role, "content": content})

    latest = copy.deepcopy(current_user_message)
    latest.pop("rag_context_str", None)
    history = history[-4:]
    history.append(latest)
    return history


def process_session(session_id):
    print(f"DEBUG: Worker started for session {session_id}")
    tmp_dir = tempfile.mkdtemp()
    last_msg = None
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
        selected_model = last_msg.get("model")
        model_supports_images = bool(selected_model and "vl" in selected_model.lower())

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

        inference_messages = _build_inference_messages(messages, last_msg)
        if processed_images and model_supports_images:
            if inference_messages and inference_messages[-1].get("role") == "user":
                inference_messages[-1]["images"] = processed_images
        elif "images" in last_msg and (not processed_images or not model_supports_images):
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
                "streaming": True,
                "visible": last_msg.get("visible", True),
            })

            full_text = ""
            stream_path = os.path.join(CHAT_DIR, session_id, "stream.txt")
            last_write = time.time()
            try:
                model_provider = last_msg.get("model_provider", "ollama")
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

            if not _update_assistant_message(session_id, timestamp, full_text, streaming=False):
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
        if last_msg and last_msg.get("role") == "user":
            safe_update_session(session_id, {
                "role": "assistant",
                "content": f"[Error during generation: {e}]",
                "timestamp": time.time(),
                "source": "hpc_worker",
                "visible": last_msg.get("visible", True),
            })
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        with processing_lock:
            processing_keys.discard(session_id)
        if _has_pending_user_message(session_id):
            ensure_session_processing(session_id)


def enqueue_chat_job(session_id, model, prompt, images, work_dir, roi_path=None, visible=True, rag_context_str=""):
    session_file = _session_path(session_id)
    with processing_lock:
        if session_id in processing_keys:
            return "busy"

    existing = _lock_and_read_session(session_file)
    existing_messages = existing.get("messages", []) if existing else []
    if existing_messages:
        current_last = existing_messages[-1]
        if current_last.get("role") == "user":
            return "busy"
        if current_last.get("role") == "assistant" and current_last.get("streaming"):
            return "busy"

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
        new_message["model_provider"] = "ollama"
        if provider_name == "ollama":
            new_message["model"] = model_name
        else:
            new_message["model"] = None
    else:
        new_message["model_provider"] = "ollama"

    if roi_path:
        new_message["roi_path"] = roi_path
    if rag_context_str:
        new_message["rag_context_str"] = rag_context_str

    duplicate = False

    def append_message(session_data):
        nonlocal duplicate
        session_data = session_data if session_data else {"session_id": session_id, "messages": []}
        messages = session_data.setdefault("messages", [])
        if messages:
            last_msg = messages[-1]
            is_recent = (new_message["timestamp"] - last_msg.get("timestamp", 0)) < 2.0
            is_same_prompt = last_msg.get("role") == "user" and last_msg.get("content") == prompt
            if is_recent and is_same_prompt:
                duplicate = True
                return session_data
        messages.append(new_message)
        session_data["updated_at"] = time.time()
        return session_data

    _lock_and_update_session(session_file, append_message)

    if duplicate:
        return "duplicate"

    # Dispatch directly — no separate polling loop needed
    ensure_session_processing(session_id)

    return "queued"
