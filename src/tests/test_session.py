from __future__ import annotations

import json
import threading

import app.session as session


def _pending_rag():
    return {
        "schema_version": 1,
        "label": "ROI",
        "intent": "literature",
        "status": "pending",
        "evidence": {"degs": [], "pathways": [], "citations": []},
        "workflow": {
            "steps": [
                {
                    "name": "Retrieved PubMed abstracts",
                    "status": "ok",
                    "tool": "pubmed_tool",
                    "detail": "",
                    "input": "ROI genes",
                    "output": "3 papers",
                }
            ],
            "tools_called": ["pubmed_tool"],
            "generation": {
                "model": "ollama:qwen2.5vl:7b",
                "status": "pending",
                "input": "120 chars of evidence context",
                "output": "handed off for streamed answer generation",
            },
        },
        "image": {"used_roi_image": True},
        "status_message": "",
    }


def test_finalize_rag_record_records_success_without_mutating_pending_workflow():
    pending = _pending_rag()

    finalized = session.finalize_rag_record(
        pending,
        success=True,
        content="A real generated answer.",
    )

    assert pending["workflow"]["generation"]["status"] == "pending"
    assert finalized["status"] == "ok"
    assert finalized["workflow"]["generation"]["status"] == "ok"
    assert finalized["workflow"]["generation"]["output"] == (
        '"A real generated answer." (24 chars)'
    )


def test_finalize_rag_record_records_real_error():
    finalized = session.finalize_rag_record(
        _pending_rag(),
        success=False,
        error="Chat generation timed out.",
    )

    assert finalized["status"] == "error"
    assert finalized["workflow"]["generation"]["status"] == "error"
    assert finalized["workflow"]["generation"]["output"] == (
        "Chat generation timed out."
    )


def test_build_rag_record_converts_agent_metadata_to_readable_schema():
    record = session.build_rag_record(
        {
            "label": "ROI",
            "intent": "pathway",
            "workflow_steps": [
                {
                    "step": "Pathway enrichment",
                    "status": "ok",
                    "tool": "pathway_tool",
                    "detail": "top pathways",
                    "input_summary": "TP53, MYC",
                    "output_summary": "Cell cycle",
                }
            ],
            "degs": [{"gene": "TP53", "log2fc": 2.1}],
            "pathways": [{"name": "Cell cycle", "neg_log10p": 4.0}],
            "citations": [],
            "tools_called": ["pathway_tool"],
            "used_roi_image": False,
        },
        generation_model="deepinfra:model",
        context_chars=321,
    )

    assert "trace" not in record
    assert record["evidence"]["degs"][0]["gene"] == "TP53"
    assert record["workflow"]["steps"][0]["name"] == "Pathway enrichment"
    assert record["workflow"]["generation"] == {
        "model": "deepinfra:model",
        "status": "pending",
        "input": "321 chars of evidence context",
        "output": "handed off for streamed answer generation",
    }


def test_session_write_read_and_append_are_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "CHAT_DIR", str(tmp_path))

    assert session._write_session("demo", {"session_id": "demo", "messages": []})
    assert session._read_session("demo") == {"schema_version": 2, "session_id": "demo", "messages": []}

    assert session.safe_update_session("demo", {"role": "user", "content": "hello"})

    data = session._read_session("demo")
    assert data["schema_version"] == 2
    assert data["session_id"] == "demo"
    assert data["messages"] == [{"role": "user", "content": "hello"}]
    assert isinstance(data["updated_at"], float)



def test_session_storage_uses_one_json_and_no_trace_files(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "CHAT_DIR", str(tmp_path))
    session._write_session(
        "demo",
        {
            "session_id": "demo",
            "messages": [
                {
                    "role": "assistant",
                    "content": "answer",
                    "rag": _pending_rag(),
                }
            ],
        },
    )

    session_dir = tmp_path / "demo"
    json_files = list(session_dir.glob("*.json"))
    assert [path.name for path in json_files] == ["session.json"]
    assert list(session_dir.glob("*.trace")) == []
    stored_text = (session_dir / "session.json").read_text()
    assert '"trace"' not in stored_text
    assert '"rag_metadata"' not in stored_text

def test_streaming_update_only_changes_last_assistant_message(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "CHAT_DIR", str(tmp_path))
    session._write_session(
        "demo",
        {
            "session_id": "demo",
            "messages": [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": ""},
            ],
        },
    )

    assert session.safe_update_streaming_message("demo", "partial answer")
    data = session._read_session("demo")
    assert data["messages"][-1]["content"] == "partial answer"
    assert data["messages"][-1]["streaming"] is True

    assert session.safe_update_streaming_message("demo", "final answer", streaming=False)
    data = session._read_session("demo")
    assert data["messages"][-1]["content"] == "final answer"
    assert "streaming" not in data["messages"][-1]


def test_last_assistant_image_update_respects_target_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "CHAT_DIR", str(tmp_path))
    session._write_session(
        "demo",
        {
            "session_id": "demo",
            "messages": [
                {"role": "assistant", "content": "old", "timestamp": 100.0},
                {"role": "assistant", "content": "new", "timestamp": 200.0},
            ],
        },
    )

    assert session.safe_update_last_assistant_image(
        "demo",
        ["roi.png"],
        target_timestamp=100.2,
    )

    data = session._read_session("demo")
    assert data["messages"][0]["images"] == ["roi.png"]
    assert "images" not in data["messages"][1]


def test_low_level_session_write_uses_valid_json_file(tmp_path):
    path = tmp_path / "session.json"
    payload = {"session_id": "demo", "messages": [{"role": "user", "content": "hi"}]}

    session._lock_and_write_session(str(path), payload)

    stored = json.loads(path.read_text())
    assert stored == {**payload, "schema_version": 2}


def test_session_read_recovers_trailing_close_brace(tmp_path):
    path = tmp_path / "session.json"
    payload = {"session_id": "demo", "messages": [{"role": "user", "content": "hi"}]}
    path.write_text(json.dumps(payload) + "}")

    assert session._lock_and_read_session(str(path)) == payload


def test_concurrent_session_appends_keep_valid_json(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "CHAT_DIR", str(tmp_path))

    threads = [
        threading.Thread(
            target=session.safe_update_session,
            args=("demo", {"role": "user", "content": f"msg-{idx}"}),
        )
        for idx in range(20)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    data = session._read_session("demo")
    assert len(data["messages"]) == 20
    assert json.loads((tmp_path / "demo" / "session.json").read_text()) == data
