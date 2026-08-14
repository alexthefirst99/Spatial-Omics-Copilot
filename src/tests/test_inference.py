from __future__ import annotations

from types import SimpleNamespace

import app.inference as inference


def test_ollama_connection_refused_returns_actionable_message(monkeypatch):
    class FakeClient:
        def __init__(self, host, timeout=None):
            self.host = host

        def chat(self, model, messages, stream, **kwargs):
            raise OSError("[Errno 61] Connection refused")

    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setattr(inference, "ollama", SimpleNamespace(Client=FakeClient))

    output = "".join(
        inference.run_model_inference(
            [{"role": "user", "content": "hi"}], provider="ollama"
        )
    )

    assert "Ollama is not reachable at http://localhost:11434" in output
    assert "ollama serve" in output
    assert "ollama pull qwen2.5vl:7b" in output


def test_deepinfra_provider_is_dispatched(monkeypatch):
    seen = {}
    monkeypatch.setenv("LLM_PROVIDER", "deepinfra")

    def fake_run(messages, model_name=None):
        seen["messages"] = messages
        seen["model"] = model_name
        yield "hosted answer"

    monkeypatch.setattr(inference, "_run_deepinfra", fake_run)

    output = "".join(
        inference.run_model_inference(
            [{"role": "user", "content": "hi"}],
            provider="deepinfra",
            model_name="Qwen/test-model",
        )
    )

    assert output == "hosted answer"
    assert seen["model"] == "Qwen/test-model"


def test_deepinfra_is_not_called_without_opt_in(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setattr(
        inference,
        "_run_deepinfra",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("called")),
    )

    output = "".join(
        inference.run_model_inference(
            [{"role": "user", "content": "hi"}], provider="deepinfra"
        )
    )

    assert "DeepInfra is disabled" in output


def test_deepinfra_images_use_openai_data_uri_format(tmp_path):
    image_path = tmp_path / "crop.png"
    image_path.write_bytes(b"small-image")

    messages = inference._deepinfra_messages(
        [{"role": "user", "content": "inspect", "images": [str(image_path)]}]
    )

    content = messages[0]["content"]
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[1] == {"type": "text", "text": "inspect"}


def test_deepinfra_can_be_the_default_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepinfra")
    monkeypatch.setenv("DEEPINFRA_MODEL", "Qwen/configured-model")

    assert inference.get_default_model_spec() == "deepinfra:Qwen/configured-model"


def test_deepinfra_http_failure_is_classified_as_inference_error():
    assert inference.is_inference_error("DeepInfra returned HTTP 404.") is True
    assert inference.is_inference_error("DeepInfra returned HTTP 503.") is True
    assert inference.is_inference_error("A grounded biological answer") is False


def test_deepinfra_call_is_logged_without_secrets(monkeypatch, capsys):
    from rag.copilot_agent import llm

    monkeypatch.setattr(
        llm,
        "call_deepinfra_chat",
        lambda payload, config: llm.LLMResponse(
            text="ok", ok=True, model=payload["model"], status_message=""
        ),
    )

    output = "".join(
        inference._run_deepinfra(
            [{"role": "user", "content": "hi"}],
            model_name="Qwen/Qwen3-VL-30B-A3B-Instruct",
        )
    )
    captured = capsys.readouterr().out

    assert output == "ok"
    assert "Calling DeepInfra" in captured
    assert "Qwen/Qwen3-VL-30B-A3B-Instruct" in captured
    assert "messages=1" in captured
    assert "response finished" in captured
