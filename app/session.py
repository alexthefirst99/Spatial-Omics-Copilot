import os
import json
import fcntl
import time
import threading
import copy

from app.config import get_path

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_DATA_DIR = os.path.join(_PROJECT_ROOT, 'data')

CHAT_DIR = get_path('paths.chat_dir', os.path.join(_DATA_DIR, 'chat_sessions'), env='COPILOT_CHAT_DIR')
os.makedirs(CHAT_DIR, exist_ok=True)


def finalize_rag_metadata(rag_metadata, *, success, content="", error=""):
    """Return a copy of ``rag_metadata`` with the synthesis step finalized.

    Live generation happens outside the agent graph, so the graph cannot know
    whether it succeeded. Keeping this transition on the server makes the
    persisted trace and every polling client agree on the final outcome.
    """

    if not isinstance(rag_metadata, dict):
        return None

    metadata = copy.deepcopy(rag_metadata)
    trace = metadata.get("trace")
    if not isinstance(trace, list):
        return metadata

    synthesis_step = next(
        (
            step
            for step in reversed(trace)
            if isinstance(step, dict) and step.get("step") == "Synthesizing answer"
        ),
        None,
    )
    if synthesis_step is None:
        return metadata

    synthesis_step["status"] = "ok" if success else "error"
    if success:
        normalized = " ".join(str(content or "").split())
        preview = normalized[:140] + ("…" if len(normalized) > 140 else "")
        synthesis_step["output_summary"] = f'"{preview}" ({len(normalized)} chars)'
    else:
        synthesis_step["output_summary"] = str(error or content or "generation failed")
    return metadata


def _ensure_parent_dir(path):
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)


def _session_lock_path(path):
    return path + ".lock"


def _read_session_unlocked(path):
    if not os.path.exists(path):
        return None
    with open(path, 'r') as f:
        text = f.read()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        decoder = json.JSONDecoder()
        try:
            data, end = decoder.raw_decode(text)
        except json.JSONDecodeError:
            raise exc
        trailing = text[end:].strip()
        if trailing and set(trailing) <= {"}"}:
            print(f"Recovered session with trailing braces: {path}")
            return data
        raise exc


def _write_session_unlocked(path, data):
    _ensure_parent_dir(path)
    tmp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
    try:
        with open(tmp_path, 'w') as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _session_path(session_id):
    session_dir = os.path.join(CHAT_DIR, session_id)
    os.makedirs(session_dir, exist_ok=True)
    return os.path.join(session_dir, "session.json")


def _lock_and_read_session(path):
    _ensure_parent_dir(path)
    with open(_session_lock_path(path), 'a+') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_SH)
        try:
            return _read_session_unlocked(path)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _lock_and_write_session(path, data):
    _ensure_parent_dir(path)
    with open(_session_lock_path(path), 'a+') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            _write_session_unlocked(path, data)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _lock_and_update_session(path, updater):
    _ensure_parent_dir(path)
    with open(_session_lock_path(path), 'a+') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            data = _read_session_unlocked(path)
            updated = updater(data)
            if updated is None:
                return False
            _write_session_unlocked(path, updated)
            return True
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _read_session(session_id):
    return _lock_and_read_session(_session_path(session_id))


def _write_session(session_id, data):
    _lock_and_write_session(_session_path(session_id), data)
    return True


def safe_update_session(session_id, new_message):
    retries = 10
    while retries > 0:
        try:
            def updater(data):
                data = data or {"session_id": session_id, "messages": []}
                data["messages"].append(new_message)
                data["updated_at"] = time.time()
                return data

            if _lock_and_update_session(_session_path(session_id), updater):
                return True
        except Exception as e:
            print(f"Async update failed: {e}")
        retries -= 1
        time.sleep(0.2 * (11 - retries))
    return False


def safe_update_streaming_message(
    session_id, content, streaming=True, rag_metadata=None
):
    retries = 5
    while retries > 0:
        try:
            def updater(data):
                if not data:
                    return None
                msgs = data.get("messages", [])
                if msgs and msgs[-1]["role"] == "assistant":
                    msgs[-1]["content"] = content
                    if rag_metadata is not None:
                        msgs[-1]["rag_metadata"] = copy.deepcopy(rag_metadata)
                    if streaming:
                        msgs[-1]["streaming"] = True
                    else:
                        msgs[-1].pop("streaming", None)
                    data["updated_at"] = time.time()
                    return data
                return None

            return _lock_and_update_session(_session_path(session_id), updater)
        except Exception as e:
            print(f"Streaming update failed: {e}")
        retries -= 1
        time.sleep(0.1)
    return False


def safe_update_last_assistant_image(session_id, image_paths, target_timestamp=None):
    retries = 10
    while retries > 0:
        try:
            def updater(data):
                if not data:
                    return None
                for msg in reversed(data.get("messages", [])):
                    if msg["role"] == "assistant":
                        if target_timestamp and abs(msg.get("timestamp", 0) - target_timestamp) > 1.0:
                            continue
                        msg["images"] = image_paths
                        data["updated_at"] = time.time()
                        return data
                return None

            if _lock_and_update_session(_session_path(session_id), updater):
                return True
        except Exception as e:
            print(f"Async image update failed: {e}")
        retries -= 1
        time.sleep(0.2 * (11 - retries))
    return False
