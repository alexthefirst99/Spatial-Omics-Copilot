from __future__ import annotations

import time

from flask import Flask

import app.routes as routes
import app.session as session
import app.worker as worker


def _pending_metadata():
    return {
        "label": "ROI",
        "trace": [
            {
                "step": "Loaded region gene expression",
                "status": "ok",
                "input_summary": "ROI",
                "output_summary": "2 differentially expressed genes",
            },
            {
                "step": "Synthesizing answer",
                "tool": "ollama:qwen2.5vl:7b",
                "status": "pending",
                "input_summary": "120 chars of evidence context",
                "output_summary": "handed off for streamed answer generation",
            },
        ],
    }


def test_enqueue_persists_pending_trace_on_user_message(tmp_path, monkeypatch):
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
        rag_metadata=_pending_metadata(),
    )

    assert status == "queued"
    stored = session._read_session("demo")["messages"][-1]
    assert stored["rag_metadata"]["trace"][-1]["status"] == "pending"


def test_worker_persists_final_trace_on_assistant_message(tmp_path, monkeypatch):
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
            "session_id": "demo",
            "messages": [
                {
                    "role": "user",
                    "content": "Explain this ROI",
                    "timestamp": 1.0,
                    "model": "qwen2.5vl:7b",
                    "model_provider": "ollama",
                    "work_dir": str(tmp_path),
                    "rag_context_str": "\n\nevidence",
                    "rag_metadata": _pending_metadata(),
                }
            ],
        },
    )

    worker.process_session("demo")

    assistant = session._read_session("demo")["messages"][-1]
    synthesis = assistant["rag_metadata"]["trace"][-1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "A grounded answer."
    assert synthesis["status"] == "ok"
    assert synthesis["output_summary"] == '"A grounded answer." (18 chars)'


def test_provider_error_text_finalizes_trace_as_error(tmp_path, monkeypatch):
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
            "session_id": "demo",
            "messages": [
                {
                    "role": "user",
                    "content": "Explain this ROI",
                    "timestamp": 1.0,
                    "model": "qwen2.5vl:7b",
                    "model_provider": "ollama",
                    "work_dir": str(tmp_path),
                    "rag_metadata": _pending_metadata(),
                }
            ],
        },
    )

    worker.process_session("demo")

    synthesis = session._read_session("demo")["messages"][-1]["rag_metadata"]["trace"][-1]
    assert synthesis["status"] == "error"
    assert "Ollama is not reachable" in synthesis["output_summary"]


def test_poll_timeout_persists_and_returns_error_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "CHAT_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "CHAT_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "_chat_stream_timeout_seconds", lambda: 0)
    session._write_session(
        "demo",
        {
            "session_id": "demo",
            "messages": [
                {
                    "role": "assistant",
                    "content": "...",
                    "timestamp": time.time() - 10,
                    "streaming": True,
                    "rag_metadata": _pending_metadata(),
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
    synthesis = payload["rag_metadata"]["trace"][-1]
    assert payload["status"] == "done"
    assert synthesis["status"] == "error"
    assert "timed out" in synthesis["output_summary"]
    persisted = session._read_session("demo")["messages"][-1]
    assert persisted["rag_metadata"]["trace"][-1]["status"] == "error"
