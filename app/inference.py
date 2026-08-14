import os

try:
    import ollama
except ImportError:  # DeepInfra deployments do not need the Ollama client.
    ollama = None

from app.config import get_config, load_config, load_dotenv

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5vl:7b"
DEFAULT_OLLAMA_TIMEOUT = 120
DEFAULT_OLLAMA_NUM_PREDICT = 48
DEFAULT_OLLAMA_KEEP_ALIVE = "10m"
DEFAULT_PROVIDER = "ollama"

_INFERENCE_ERROR_MARKERS = (
    "ollama is not reachable",
    "error querying ollama",
    "error during generation",
    "ollama python client is not installed",
    "deepinfra is not configured",
    "deepinfra is disabled",
    "no deepinfra model configured",
    "deepinfra request failed",
    "deepinfra rejected the api key",
    "deepinfra returned http",
    "deepinfra returned a non-json response",
    "deepinfra returned no completion choices",
    "deepinfra returned an empty completion",
    "deepinfra call failed",
    "the requests library is required to call deepinfra",
    "unsupported model provider",
)


def is_inference_error(text):
    """Return whether model output is one of this module's error messages."""

    lowered = str(text or "").strip().lower()
    return any(marker in lowered for marker in _INFERENCE_ERROR_MARKERS)


def deepinfra_enabled():
    """Return whether the hosted provider was explicitly enabled in `.env`."""

    load_dotenv()
    return str(os.environ.get("LLM_PROVIDER") or "").strip().lower() == "deepinfra"


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


def get_default_model_spec():
    """Return the configured ``provider:model`` value used by the chat UI."""

    if deepinfra_enabled():
        provider = "deepinfra"
        model = get_config("deepinfra.model", "", env="DEEPINFRA_MODEL")
    else:
        provider = "ollama"
        model = get_config(
            "ollama.model", DEFAULT_OLLAMA_MODEL, env="OLLAMA_MODEL"
        )
    return f"{provider}:{str(model or '').strip()}"


def _deepinfra_messages(messages):
    """Translate local image paths to OpenAI-compatible base64 image parts."""

    from rag.copilot_agent.multimodal import encode_image_data_uri

    prepared = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")
        image_parts = []
        for image_path in msg.get("images", []):
            data_uri = encode_image_data_uri(image_path)
            if data_uri:
                image_parts.append(
                    {"type": "image_url", "image_url": {"url": data_uri}}
                )

        if image_parts:
            text_parts = (
                content
                if isinstance(content, list)
                else [{"type": "text", "text": str(content)}]
            )
            content = [*image_parts, *text_parts]
        prepared.append({"role": role, "content": content})
    return prepared


def _run_deepinfra(messages, model_name=None):
    from rag.copilot_agent.llm import call_deepinfra_chat, resolve_model

    config = load_config()
    selected_model = (model_name or "").strip() or resolve_model(config)
    prepared_messages = _deepinfra_messages(messages)
    image_count = sum(
        1
        for message in prepared_messages
        for part in (
            message.get("content")
            if isinstance(message.get("content"), list)
            else []
        )
        if isinstance(part, dict) and part.get("type") == "image_url"
    )
    print(
        "DEBUG: Calling DeepInfra "
        f"(model={selected_model or 'unset'}, messages={len(prepared_messages)}, "
        f"images={image_count})",
        flush=True,
    )
    response = call_deepinfra_chat(
        {"messages": prepared_messages, "model": selected_model},
        config,
    )
    if response.ok:
        print(
            f"DEBUG: DeepInfra response finished (model={selected_model}, "
            f"characters={len(response.text)})",
            flush=True,
        )
        yield response.text
    else:
        status = response.status_message or "DeepInfra call failed."
        print(f"DeepInfra Error: {status}", flush=True)
        yield status


def run_model_inference(messages, provider=None, model_name=None):
    configured_provider = "deepinfra" if deepinfra_enabled() else DEFAULT_PROVIDER
    provider = str(provider or configured_provider).strip().lower()
    if provider == "deepinfra":
        if configured_provider != "deepinfra":
            yield "DeepInfra is disabled. Set LLM_PROVIDER=deepinfra in .env and restart the app."
            return
        yield from _run_deepinfra(messages, model_name=model_name)
        return
    if provider != "ollama":
        yield f"Unsupported model provider: {provider}."
        return

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

    if ollama is None:
        yield (
            "The Ollama Python client is not installed. Install project dependencies "
            "or select the DeepInfra provider."
        )
        return

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
