import os
import ollama

from app.config import get_config

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5vl:7b"


def _format_ollama_error(error, host, model_name):
    message = str(error)
    connection_markers = (
        "connection refused",
        "failed to connect",
        "could not connect",
        "connect error",
        "errno 61",
        "errno 111",
    )
    if any(marker in message.lower() for marker in connection_markers):
        return (
            "Ollama is not reachable at "
            f"{host}. Start Ollama with `ollama serve`, or set OLLAMA_HOST to the "
            "server URL. Then make sure the selected model is available with "
            f"`ollama pull {model_name}`."
        )
    return f"Error querying Ollama: {message}"


def run_model_inference(messages, provider=None, model_name=None):
    selected_model = model_name or get_config("ollama.model", DEFAULT_OLLAMA_MODEL, env="OLLAMA_MODEL")
    host = get_config("ollama.host", DEFAULT_OLLAMA_HOST, env="OLLAMA_HOST")
    os.environ["OLLAMA_HOST"] = host

    clean_history = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        clean_msg = {"role": role, "content": content}
        valid_images = [img for img in msg.get("images", []) if os.path.exists(img)]
        if valid_images:
            clean_msg["images"] = valid_images
        clean_history.append(clean_msg)

    try:
        print(f"DEBUG: Calling Ollama (model={selected_model}, history={len(clean_history)})")
        client = ollama.Client(host=host)
        stream = client.chat(model=selected_model, messages=clean_history, stream=True)
        chunk_count = 0
        for chunk in stream:
            chunk_count += 1
            yield chunk["message"]["content"]
        print(f"DEBUG: Ollama stream finished. Chunks={chunk_count}")
    except Exception as e:
        print(f"Ollama Error: {e}")
        yield _format_ollama_error(e, host, selected_model)
