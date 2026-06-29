import os
import json
import fcntl
import time

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

CHAT_DIR = os.environ.get('COPILOT_CHAT_DIR', os.path.join(_PROJECT_ROOT, 'chat_sessions'))
os.makedirs(CHAT_DIR, exist_ok=True)


def _session_path(session_id):
    session_dir = os.path.join(CHAT_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    return os.path.join(session_dir, "session.json")


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


def _read_session(session_id):
    return _lock_and_read_session(_session_path(session_id))


def _write_session(session_id, data):
    _lock_and_write_session(_session_path(session_id), data)
    return True


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
