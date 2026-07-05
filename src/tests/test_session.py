from __future__ import annotations

import json
import threading

import app.session as session


def test_session_write_read_and_append_are_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(session, "CHAT_DIR", str(tmp_path))

    assert session._write_session("demo", {"session_id": "demo", "messages": []})
    assert session._read_session("demo") == {"session_id": "demo", "messages": []}

    assert session.safe_update_session("demo", {"role": "user", "content": "hello"})

    data = session._read_session("demo")
    assert data["session_id"] == "demo"
    assert data["messages"] == [{"role": "user", "content": "hello"}]
    assert isinstance(data["updated_at"], float)


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

    assert json.loads(path.read_text()) == payload


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
