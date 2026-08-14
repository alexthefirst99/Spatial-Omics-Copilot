from __future__ import annotations

import json
import time

from flask import Flask

import app.routes as routes
import app.session as session
import app.worker as worker


def _pending_rag():
    return {
        "schema_version": 1,
        "label": "ROI",
        "intent": "literature",
        "status": "pending",
        "evidence": {
            "degs": [{"gene": "TP53", "log2fc": 2.1}],
            "pathways": [],
            "citations": [],
        },
        "workflow": {
            "steps": [
                {
                    "name": "Loaded region gene expression",
                    "status": "ok",
                    "tool": "",
                    "detail": "",
                    "input": "ROI",
                    "output": "2 differentially expressed genes",
                }
            ],
            "tools_called": [],
            "generation": {
                "model": "ollama:qwen2.5vl:7b",
                "status": "pending",
                "input": "120 chars of evidence context",
                "output": "handed off for streamed answer generation",
            },
        },
        "image": {"used_roi_image": False},
        "status_message": "",
    }


def test_enqueue_persists_one_pending_rag_record_on_user_message(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "CHAT_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "CHAT_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "ensure_session_processing", lambda _session_id: True)
    worker.processing_keys.clear()

    status = worker.enqueue_chat_job(
        session_id="demo",
        model="ollama:qwen2.5vl:7b",
        prompt="Explain this ROI",
        images=[],
        work_dir=str(tmp_path),
        rag_context_str="\n\nevidence",
        rag=_pending_rag(),
    )

    assert status == "queued"
    stored = session._read_session("demo")
    assert stored["schema_version"] == 2
    assert stored["messages"][-1]["rag"]["workflow"]["generation"]["status"] == "pending"
    assert "rag_metadata" not in stored["messages"][-1]
    assert "trace" not in json.dumps(stored)


def test_worker_moves_rag_to_assistant_and_compacts_user_message(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "CHAT_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "CHAT_DIR", str(tmp_path))
    monkeypatch.setattr(
        worker,
        "run_model_inference",
        lambda *args, **kwargs: iter(["A grounded answer."]),
    )
    worker.processing_keys.clear()

    session._write_session(
        "demo",
        {
            "schema_version": 2,
            "session_id": "demo",
            "messages": [
                {
                    "role": "user",
                    "content": "Explain this ROI",
                    "timestamp": 1.0,
                    "model": "qwen2.5vl:7b",
                    "model_provider": "ollama",
                    "work_dir": str(tmp_path),
                    "src_images": [],
                    "rag_context_str": "\n\nevidence",
                    "rag": _pending_rag(),
                }
            ],
        },
    )

    worker.process_session("demo")

    payload = session._read_session("demo")
    user, assistant = payload["messages"][-2:]
    generation = assistant["rag"]["workflow"]["generation"]

    assert assistant["role"] == "assistant"
    assert assistant["content"] == "A grounded answer."
    assert assistant["rag"]["status"] == "ok"
    assert generation["status"] == "ok"
    assert generation["output"] == '"A grounded answer." (18 chars)'
    assert "rag" not in user
    assert "rag_context_str" not in user
    assert "work_dir" not in user
    assert "src_images" not in user
    assert "trace" not in json.dumps(payload)


def test_provider_error_finalizes_generation_as_error(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "CHAT_DIR", str(tmp_path))
    monkeypatch.setattr(worker, "CHAT_DIR", str(tmp_path))
    monkeypatch.setattr(
        worker,
        "run_model_inference",
        lambda *args, **kwargs: iter(["Ollama is not reachable at http://localhost:11434."]),
    )
    worker.processing_keys.clear()

    session._write_session(
        "demo",
        {
            "schema_version": 2,
            "session_id": "demo",
            "messages": [
                {
                    "role": "user",
                    "content": "Explain this ROI",
                    "timestamp": 1.0,
                    "model": "qwen2.5vl:7b",
                    "model_provider": "ollama",
                    "work_dir": str(tmp_path),
                    "rag": _pending_rag(),
                }
            ],
        },
    )

    worker.process_session("demo")

    generation = session._read_session("demo")["messages"][-1]["rag"]["workflow"]["generation"]
    assert generation["status"] == "error"
    assert "Ollama is not reachable" in generation["output"]


def test_poll_timeout_persists_and_returns_error_workflow(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "CHAT_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "CHAT_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "_chat_stream_timeout_seconds", lambda: 0)
    session._write_session(
        "demo",
        {
            "schema_version": 2,
            "session_id": "demo",
            "messages": [
                {
                    "role": "assistant",
                    "content": "...",
                    "timestamp": time.time() - 10,
                    "streaming": True,
                    "rag": _pending_rag(),
                }
            ],
        },
    )

    app = Flask(__name__)
    routes.register_chat_routes(
        app,
        workspace_id="demo",
        work_dir=str(tmp_path),
        base_path="/workspaces/demo",
    )

    response = app.test_client().get("/workspaces/demo/chat/poll")

    payload = response.get_json()
    generation = payload["rag"]["workflow"]["generation"]
    assert payload["status"] == "done"
    assert generation["status"] == "error"
    assert "timed out" in generation["output"]
    persisted = session._read_session("demo")["messages"][-1]
    assert persisted["rag"]["workflow"]["generation"]["status"] == "error"
    assert "trace" not in json.dumps(session._read_session("demo"))
