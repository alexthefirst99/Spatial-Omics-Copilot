import os
import ollama

from app.config import get_config

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5vl:7b"
DEFAULT_OLLAMA_TIMEOUT = 120
DEFAULT_OLLAMA_NUM_PREDICT = 48
DEFAULT_OLLAMA_KEEP_ALIVE = "10m"


def _get_int_config(key, default, env):
    try:
        return int(get_config(key, default, env=env))
    except (TypeError, ValueError):
        return default


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
    timeout = _get_int_config("ollama.timeout", DEFAULT_OLLAMA_TIMEOUT, env="OLLAMA_TIMEOUT")
    num_predict = _get_int_config("ollama.num_predict", DEFAULT_OLLAMA_NUM_PREDICT, env="OLLAMA_NUM_PREDICT")
    keep_alive = get_config("ollama.keep_alive", DEFAULT_OLLAMA_KEEP_ALIVE, env="OLLAMA_KEEP_ALIVE")
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
        print(f"DEBUG: Calling Ollama (model={selected_model}, history={len(clean_history)}, timeout={timeout}s)")
        client = ollama.Client(host=host, timeout=timeout)
        stream = client.chat(
            model=selected_model,
            messages=clean_history,
            stream=True,
            options={
                "num_predict": num_predict,
                "temperature": 0.2,
            },
            keep_alive=keep_alive,
        )
        chunk_count = 0
        for chunk in stream:
            chunk_count += 1
            yield chunk["message"]["content"]
        print(f"DEBUG: Ollama stream finished. Chunks={chunk_count}")
    except Exception as e:
        print(f"Ollama Error: {e}")
        yield _format_ollama_error(e, host, selected_model)
