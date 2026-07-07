from __future__ import annotations

import app.inference as inference


def test_ollama_connection_refused_returns_actionable_message(monkeypatch):
    class FakeClient:
        def __init__(self, host):
            self.host = host

        def chat(self, model, messages, stream):
            raise OSError("[Errno 61] Connection refused")

    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setattr(inference.ollama, "Client", FakeClient)

    output = "".join(inference.run_model_inference([{"role": "user", "content": "hi"}]))

    assert "Ollama is not reachable at http://localhost:11434" in output
    assert "ollama serve" in output
    assert "ollama pull qwen2.5vl:7b" in output
