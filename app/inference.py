import os
import json
import ssl
import urllib.request
import urllib.error
import base64
import mimetypes
import ollama

from app.config import get_bool, get_config, load_dotenv

DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5vl:7b"
DEFAULT_OPENAI_MODEL = "gpt-4o"


def _image_to_data_url(image_path):
    mime_type = mimetypes.guess_type(image_path)[0] or "image/png"
    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _openai_messages_from_history(messages):
    openai_messages = []
    for msg in messages:
        role = msg.get("role")
        if role not in ("system", "user", "assistant"):
            continue

        content = msg.get("content", "")
        valid_images = [img for img in msg.get("images", []) if os.path.exists(img)]
        if valid_images and role == "user":
            parts = [{"type": "text", "text": content or ""}]
            for img in valid_images:
                parts.append({
                    "type": "image_url",
                    "image_url": {"url": _image_to_data_url(img)}
                })
            openai_messages.append({"role": role, "content": parts})
        else:
            openai_messages.append({"role": role, "content": content or ""})
    return openai_messages


def _stream_openai_chat(messages, model_name):
    load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        yield "OpenAI API key is not set. Please set OPENAI_API_KEY before using ChatGPT API."
        return

    payload = {
        "model": model_name,
        "messages": _openai_messages_from_history(messages),
        "stream": True,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    def stream_with_context(ssl_context):
        with urllib.request.urlopen(req, timeout=120, context=ssl_context) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data: "):
                    continue
                event = line[6:]
                if event == "[DONE]":
                    break
                try:
                    chunk = json.loads(event)
                    delta = chunk["choices"][0].get("delta", {})
                    text = delta.get("content")
                    if text:
                        yield text
                except Exception as e:
                    print(f"OpenAI stream parse error: {e}")

    try:
        ssl_context = None
        if get_bool("openai.insecure_ssl", default=False, env="OPENAI_INSECURE_SSL"):
            print("WARNING: OPENAI_INSECURE_SSL=1, SSL certificate verification is disabled for OpenAI requests.")
            ssl_context = ssl._create_unverified_context()
        else:
            try:
                import certifi
                ssl_context = ssl.create_default_context(cafile=certifi.where())
            except Exception as cert_error:
                print(f"OpenAI SSL context fallback: {cert_error}")

        try:
            yield from stream_with_context(ssl_context)
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" not in str(e) and "CERTIFICATEVERIFYFAILED" not in str(e):
                raise
            print("WARNING: OpenAI SSL verification failed; retrying once without certificate verification for local dev.")
            yield from stream_with_context(ssl._create_unverified_context())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"OpenAI HTTP Error: {e.code} {body}")
        yield f"OpenAI API error {e.code}: {body}"
    except Exception as e:
        print(f"OpenAI Error: {e}")
        yield f"Error querying OpenAI: {e}"


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
    provider = (provider or "ollama").lower()

    if provider == "openai":
        selected_model = model_name or get_config("openai.model", DEFAULT_OPENAI_MODEL, env="OPENAI_MODEL")
        print(f"DEBUG: Calling OpenAI (model={selected_model}, history={len(messages)})")
        yield from _stream_openai_chat(messages, selected_model)
        return

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
