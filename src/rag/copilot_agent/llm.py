"""DeepInfra chat completion client (T-047).

DeepInfra exposes an OpenAI-compatible chat-completions API, so this is a thin
``requests`` wrapper — no new dependency is introduced (``requests`` is already
a project dependency; the ``openai`` package is not installed).

Boundaries this module respects:

* **It does not import from ``app/``.** ``docs/rules.md`` section 3 keeps
  ``src/rag/`` free of app infrastructure. Configuration arrives as a plain
  dict from the caller, and secrets are read from the environment directly.
* **It does not stream.** Token streaming, session writes and the chat
  transport stay in ``app/worker.py``. This returns a complete string, which is
  what the task plan's ``call_deepinfra_model(prompt_payload, config) -> str``
  specifies.
* **It never logs the API key**, and never includes it in an error message
  (``docs/rules.md`` section 2).

The default provider for the running app is still local Ollama; DeepInfra is
opt-in via ``DEEPINFRA_API_KEY`` / the ``deepinfra`` config block, so nothing
starts calling a paid API by merely importing this module.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from math import isfinite
from typing import Any

#: OpenAI-compatible base URL. Overridable through ``deepinfra.base_url``.
DEFAULT_BASE_URL = "https://api.deepinfra.com/v1/openai"

DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_TOKENS = 512
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_RETRIES = 2

#: Environment variables checked for the API key, in order. DeepInfra's own
#: docs call it DEEPINFRA_TOKEN; this project's .env uses DEEPINFRA_API_KEY.
_API_KEY_ENV_VARS = ("DEEPINFRA_API_KEY", "DEEPINFRA_TOKEN")

#: Environment variables checked for the model id, in order. ``LLM_MODEL`` is
#: this team's existing convention and is checked first so the shared .env
#: works without duplicating the value.
_MODEL_ENV_VARS = ("LLM_MODEL", "DEEPINFRA_MODEL")

#: Statuses worth retrying: rate limiting and transient server errors.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(slots=True)
class LLMResponse:
    """A completed (or failed) model call.

    Attributes:
        text: The generated answer, or "" on failure.
        ok: Whether a usable answer came back.
        status_message: Human-readable outcome, safe to show a user. Never
            contains the API key.
        model: The model that was requested.
        finish_reason: Provider-reported stop reason, when available.
    """

    text: str = ""
    ok: bool = False
    status_message: str = ""
    model: str = ""
    finish_reason: str = ""


class DeepInfraNotConfigured(RuntimeError):
    """Raised only by :func:`require_deepinfra_config`, never by the callers."""


def _as_int(value: object, default: int) -> int:
    """Coerce to int, falling back to ``default`` rather than raising."""

    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: object, default: float) -> float:
    """Coerce to a finite float, falling back to ``default`` rather than raising."""

    if isinstance(value, bool) or value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _section(config: object | None, name: str) -> dict:
    """Return ``config[name]`` as a dict, or an empty dict."""

    if not isinstance(config, dict):
        return {}
    block = config.get(name)
    return block if isinstance(block, dict) else {}


def _setting(config: object | None, key: str, default: Any, env: str | None = None) -> Any:
    """Resolve one setting: environment first, then config, then default.

    Environment wins so that a deployment can override a checked-in config
    without editing it — the same precedence ``app/config.py`` uses.
    """

    if env:
        value = os.environ.get(env)
        if value not in (None, ""):
            return value

    block = _section(config, "deepinfra")
    if key in block and block[key] not in (None, ""):
        return block[key]

    if isinstance(config, dict) and key in config and config[key] not in (None, ""):
        return config[key]

    return default


def resolve_api_key(config: object | None = None) -> str:
    """Return the DeepInfra API key from the environment or config, or "".

    Checked in order: ``DEEPINFRA_API_KEY``, ``DEEPINFRA_TOKEN``, then
    ``deepinfra.api_key``. Keys belong in ``.env`` — the config fallback exists
    for tests and for deployments that inject config from a secret manager.
    """

    for env_var in _API_KEY_ENV_VARS:
        value = os.environ.get(env_var)
        if value:
            return value.strip()

    key = _section(config, "deepinfra").get("api_key")
    return str(key).strip() if key else ""


def resolve_model(config: object | None = None) -> str:
    """Return the configured DeepInfra model identifier, or "".

    No default model id is hardcoded: DeepInfra's catalogue changes, and
    silently calling a model the user did not choose is worse than a clear
    "not configured" error.
    """

    for env_var in _MODEL_ENV_VARS:
        value = os.environ.get(env_var)
        if value:
            return value.strip()
    return str(_setting(config, "model", "") or "").strip()


def is_configured(config: object | None = None) -> bool:
    """Whether both an API key and a model are available."""

    return bool(resolve_api_key(config)) and bool(resolve_model(config))


def require_deepinfra_config(config: object | None = None) -> tuple[str, str]:
    """Return ``(api_key, model)`` or raise :class:`DeepInfraNotConfigured`."""

    api_key = resolve_api_key(config)
    model = resolve_model(config)
    if not api_key:
        raise DeepInfraNotConfigured(
            "No DeepInfra API key. Set DEEPINFRA_API_KEY in .env."
        )
    if not model:
        raise DeepInfraNotConfigured(
            "No DeepInfra model configured. Set deepinfra.model in config/app.yaml "
            "or DEEPINFRA_MODEL in .env."
        )
    return api_key, model


def call_deepinfra_chat(
    prompt_payload: dict,
    config: dict | None = None,
    *,
    timeout: float | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    max_retries: int | None = None,
) -> LLMResponse:
    """Call DeepInfra's chat-completions endpoint.

    Args:
        prompt_payload: Payload from
            :func:`rag.copilot_agent.multimodal.build_multimodal_prompt_payload`,
            or any dict carrying ``messages`` and optionally ``model``.
        config: Application config. Reads the ``deepinfra`` block.
        timeout: Per-request timeout in seconds.
        max_tokens: Generation cap.
        temperature: Sampling temperature. Low by default — this is an
            evidence-grounded summarisation task, not creative writing.
        max_retries: Additional attempts on 429 / 5xx responses.

    Returns:
        An :class:`LLMResponse`. Never raises for network, auth or parse
        failures; the reason lands in ``status_message`` instead, so a failed
        call degrades the turn rather than losing it.
    """

    messages = (prompt_payload or {}).get("messages")
    if not messages:
        return LLMResponse(status_message="No messages to send to the model.")

    model = str((prompt_payload or {}).get("model") or "").strip() or resolve_model(config)
    api_key = resolve_api_key(config)

    if not api_key:
        return LLMResponse(
            model=model,
            status_message=(
                "DeepInfra is not configured — set DEEPINFRA_API_KEY in .env."
            ),
        )
    if not model:
        return LLMResponse(
            status_message=(
                "No DeepInfra model configured — set deepinfra.model in "
                "config/app.yaml or DEEPINFRA_MODEL in .env."
            ),
        )

    try:
        import requests
    except ImportError:
        return LLMResponse(
            model=model,
            status_message="The requests library is required to call DeepInfra.",
        )

    base_url = str(_setting(config, "base_url", DEFAULT_BASE_URL, env="DEEPINFRA_BASE_URL"))
    url = base_url.rstrip("/") + "/chat/completions"

    # config/app.yaml is hand-edited, so every numeric setting is coerced
    # defensively. A bare int()/float() here would raise on `temperature: hot`
    # and escape the turn rather than degrading it.
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": _as_int(
            max_tokens
            if max_tokens is not None
            else _setting(config, "max_tokens", DEFAULT_MAX_TOKENS),
            DEFAULT_MAX_TOKENS,
        ),
        "temperature": _as_float(
            temperature
            if temperature is not None
            else _setting(config, "temperature", DEFAULT_TEMPERATURE),
            DEFAULT_TEMPERATURE,
        ),
        "stream": False,
    }

    request_timeout = _as_float(
        timeout if timeout is not None else _setting(config, "timeout", DEFAULT_TIMEOUT),
        DEFAULT_TIMEOUT,
    )
    attempts = _as_int(
        max_retries
        if max_retries is not None
        else _setting(config, "max_retries", DEFAULT_MAX_RETRIES),
        DEFAULT_MAX_RETRIES,
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_status = ""
    for attempt in range(max(1, attempts + 1)):
        try:
            response = requests.post(
                url, json=body, headers=headers, timeout=request_timeout
            )
        except Exception as exc:  # noqa: BLE001 — never propagate to the turn.
            # The exception text can echo the request; report only the type so
            # a bearer token can never reach a log or the UI.
            last_status = f"DeepInfra request failed ({type(exc).__name__})."
            if attempt < attempts:
                time.sleep(_backoff(attempt))
                continue
            return LLMResponse(model=model, status_message=last_status)

        if response.status_code in _RETRYABLE_STATUS and attempt < attempts:
            time.sleep(_retry_delay(response, attempt))
            last_status = f"DeepInfra returned HTTP {response.status_code}."
            continue

        return _parse_response(response, model)

    return LLMResponse(model=model, status_message=last_status or "DeepInfra call failed.")


def _backoff(attempt: int) -> float:
    """Exponential backoff, capped so a retry cannot stall a request path."""

    return min(2.0 ** attempt, 8.0)


def _retry_delay(response: Any, attempt: int) -> float:
    """Honour ``Retry-After`` when present, else back off exponentially.

    Both ends are clamped and non-finite values are rejected. A server (or a
    proxy in front of it) answering ``Retry-After: -1`` would otherwise reach
    ``time.sleep(-1.0)``, which raises ``ValueError`` outside the try that
    guards the request — killing the whole agent turn instead of degrading it.
    ``NaN`` and ``inf`` fail the same way, and an unbounded positive value
    would stall the request path.
    """

    header = ""
    try:
        header = response.headers.get("Retry-After", "")
    except Exception:  # noqa: BLE001 - a stubbed response may lack headers.
        header = ""
    try:
        delay = float(header)
    except (TypeError, ValueError):
        return _backoff(attempt)
    if not isfinite(delay):
        return _backoff(attempt)
    return min(max(delay, 0.0), 10.0)


def _parse_response(response: Any, model: str) -> LLMResponse:
    """Turn an HTTP response into an :class:`LLMResponse`."""

    status_code = getattr(response, "status_code", 0)

    if status_code == 401 or status_code == 403:
        return LLMResponse(
            model=model,
            status_message="DeepInfra rejected the API key (HTTP %d)." % status_code,
        )
    if status_code >= 400:
        return LLMResponse(
            model=model,
            status_message=f"DeepInfra returned HTTP {status_code}.",
        )

    try:
        data = response.json()
    except Exception:  # noqa: BLE001 — a malformed body is not fatal.
        return LLMResponse(
            model=model, status_message="DeepInfra returned a non-JSON response."
        )

    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices:
        return LLMResponse(
            model=model, status_message="DeepInfra returned no completion choices."
        )

    # An error page, a proxy, or a partially OpenAI-compatible provider can
    # return 200 with choices like ["service unavailable"] or
    # [{"message": "boom"}]. Attribute access on those raises AttributeError
    # outside any try, which would escape all the way out of the agent turn.
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message")
    if not isinstance(message, dict):
        message = {}
    content = message.get("content")

    # Some OpenAI-compatible providers return content as a list of parts.
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )

    text = (content or "").strip()
    if not text:
        return LLMResponse(
            model=model,
            finish_reason=str(first.get("finish_reason") or ""),
            status_message="DeepInfra returned an empty completion.",
        )

    return LLMResponse(
        text=text,
        ok=True,
        model=model,
        finish_reason=str(first.get("finish_reason") or ""),
        status_message="",
    )


def call_deepinfra_model(prompt_payload: dict, config: dict | None = None) -> str:
    """Return the model's answer text, or "" when the call did not succeed.

    Thin wrapper matching the signature named in the task plan. Callers that
    need to distinguish "the model declined" from "the call failed" should use
    :func:`call_deepinfra_chat` and read ``status_message``.
    """

    return call_deepinfra_chat(prompt_payload, config).text


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT",
    "DeepInfraNotConfigured",
    "LLMResponse",
    "call_deepinfra_chat",
    "call_deepinfra_model",
    "is_configured",
    "require_deepinfra_config",
    "resolve_api_key",
    "resolve_model",
]
