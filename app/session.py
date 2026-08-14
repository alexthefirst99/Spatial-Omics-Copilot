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


def _workflow_status(record):
    workflow = record.get("workflow") if isinstance(record, dict) else None
    if not isinstance(workflow, dict):
        return "idle"

    generation = workflow.get("generation")
    if isinstance(generation, dict):
        status = generation.get("status")
        if status in {"pending", "error", "ok"}:
            return status

    steps = workflow.get("steps")
    if isinstance(steps, list):
        statuses = {
            step.get("status")
            for step in steps
            if isinstance(step, dict) and step.get("status")
        }
        if "error" in statuses:
            return "error"
        if steps:
            return "ok"
    return "idle"


def build_rag_record(agent_metadata, *, generation_model="", context_chars=0):
    """Convert agent metadata into the persisted/UI RAG schema.

    The agent graph still uses its internal ``TraceStep`` objects, but chat
    storage intentionally does not expose or persist a ``*.trace`` field.
    Each message instead stores a readable ``rag`` object with evidence and a
    workflow split into analysis/tool ``steps`` plus one ``generation`` block.
    """

    if not isinstance(agent_metadata, dict):
        return None

    # Already in the new schema: return a defensive copy and only fill the
    # generation handoff when the caller explicitly supplies a model.
    if isinstance(agent_metadata.get("workflow"), dict) and isinstance(
        agent_metadata.get("evidence"), dict
    ):
        record = copy.deepcopy(agent_metadata)
    else:
        legacy_steps = agent_metadata.get("workflow_steps")
        if legacy_steps is None:
            # Read-only compatibility for sessions created before schema v2.
            legacy_steps = agent_metadata.get("trace")
        steps = []
        generation = None
        if isinstance(legacy_steps, list):
            for item in legacy_steps:
                if not isinstance(item, dict):
                    continue
                if item.get("step") == "Synthesizing answer":
                    generation = {
                        "model": str(item.get("tool") or ""),
                        "status": str(item.get("status") or "pending"),
                        "input": str(item.get("input_summary") or ""),
                        "output": str(item.get("output_summary") or ""),
                    }
                    continue
                step = {
                    "name": str(item.get("step") or "Workflow step"),
                    "status": str(item.get("status") or "ok"),
                }
                optional_fields = {
                    "tool": item.get("tool"),
                    "detail": item.get("detail"),
                    "input": item.get("input_summary"),
                    "output": item.get("output_summary"),
                }
                for key, value in optional_fields.items():
                    text = str(value or "").strip()
                    if text:
                        step[key] = text
                steps.append(step)

        record = {
            "schema_version": 1,
            "label": str(agent_metadata.get("label") or "selection"),
            "intent": str(agent_metadata.get("intent") or ""),
            "status": "idle",
            "evidence": {
                "degs": copy.deepcopy(agent_metadata.get("degs") or []),
                "pathways": copy.deepcopy(agent_metadata.get("pathways") or []),
                "citations": copy.deepcopy(agent_metadata.get("citations") or []),
            },
            "workflow": {
                "steps": steps,
                "tools_called": list(agent_metadata.get("tools_called") or []),
                "generation": generation,
            },
            "image": {
                "used_roi_image": bool(agent_metadata.get("used_roi_image", False)),
            },
        }
        status_message = str(agent_metadata.get("status_message") or "").strip()
        if status_message:
            record["status_message"] = status_message

    if generation_model:
        workflow = record.setdefault("workflow", {})
        workflow["generation"] = {
            "model": str(generation_model),
            "status": "pending",
            "input": f"{int(context_chars or 0)} chars of evidence context",
            "output": "handed off for streamed answer generation",
        }

    record["status"] = _workflow_status(record)
    return record


def finalize_rag_record(rag_record, *, success, content="", error=""):
    """Finalize the generation block without mutating the pending record."""

    record = build_rag_record(rag_record)
    if not isinstance(record, dict):
        return None

    workflow = record.setdefault("workflow", {})
    generation = workflow.get("generation")
    if not isinstance(generation, dict):
        generation = {
            "model": "",
            "status": "pending",
            "input": "",
            "output": "",
        }
        workflow["generation"] = generation

    generation["status"] = "ok" if success else "error"
    if success:
        normalized = " ".join(str(content or "").split())
        preview = normalized[:140] + ("…" if len(normalized) > 140 else "")
        generation["output"] = f'"{preview}" ({len(normalized)} chars)'
    else:
        generation["output"] = str(error or content or "generation failed")

    record["status"] = _workflow_status(record)
    return record



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


def _normalize_session_for_storage(data):
    """Return the canonical schema-v2 session document written to disk."""

    if not isinstance(data, dict):
        return data
    payload = copy.deepcopy(data)
    payload["schema_version"] = 2
    for message in payload.get("messages", []):
        if not isinstance(message, dict):
            continue
        legacy = message.pop("rag_metadata", None)
        current = message.get("rag")
        if current is not None:
            message["rag"] = build_rag_record(current)
        elif legacy is not None:
            message["rag"] = build_rag_record(legacy)
    return payload


def _write_session_unlocked(path, data):
    _ensure_parent_dir(path)
    data = _normalize_session_for_storage(data)
    tmp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
    try:
        with open(tmp_path, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
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
                data = data or {"schema_version": 2, "session_id": session_id, "messages": []}
                data["messages"].append(new_message)
                data["schema_version"] = max(int(data.get("schema_version") or 1), 2)
                data["updated_at"] = time.time()
                return data

            if _lock_and_update_session(_session_path(session_id), updater):
                return True
        except Exception as e:
            print(f"Async update failed: {e}")
        retries -= 1
        time.sleep(0.2 * (11 - retries))
    return False


def safe_begin_assistant_message(session_id, new_message):
    """Append an assistant message and compact the completed user request.

    RAG workflow data is moved to the assistant message, so a completed turn
    has one readable copy in ``session.json`` instead of duplicate metadata on
    both the user and assistant messages.
    """

    def updater(data):
        if not data:
            return None
        messages = data.setdefault("messages", [])
        if messages and messages[-1].get("role") == "user":
            user_msg = messages[-1]
            for key in ("rag", "rag_metadata", "rag_context_str", "work_dir", "src_images", "roi_path"):
                user_msg.pop(key, None)
        messages.append(copy.deepcopy(new_message))
        data["schema_version"] = max(int(data.get("schema_version") or 1), 2)
        data["updated_at"] = time.time()
        return data

    return _lock_and_update_session(_session_path(session_id), updater)


def safe_update_streaming_message(
    session_id, content, streaming=True, rag=None
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
                    if rag is not None:
                        msgs[-1]["rag"] = build_rag_record(rag)
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
