"""Multimodal prompt payload construction (T-046).

Builds the request payload for a chat model, attaching the cropped ROI image
when — and only when — the configured model can actually see it.

That condition matters more than it looks. ``docs/tech.md`` and T-048 both ask
whether the final answer genuinely connects tissue appearance to gene
expression, and the cheapest way to fail that evaluation is to send a
text-only model a prompt that talks about "the image below". So vision support
is resolved up front, and :func:`build_multimodal_prompt_payload` reports what
it decided through ``image_included`` — which the prompt builder then uses to
either permit or forbid visual claims.

The payload follows the OpenAI chat-completions schema, which DeepInfra
implements. Image parts are inlined as ``data:`` URIs rather than URLs: the
crops live on the local filesystem (or HPC scratch) and are never publicly
addressable, and ``docs/rules.md`` section 2 treats tissue images as sensitive.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from typing import Any

from rag.copilot_agent import adapters
from rag.copilot_agent.prompt import build_evidence_context

#: Substrings that identify a vision-capable model across the providers this
#: project might use. Checked case-insensitively against the model name.
_VISION_MARKERS = (
    "vl",          # qwen2.5vl, qwen2-vl
    "vision",      # llama-3.2-90b-vision-instruct
    "llava",
    "pixtral",
    "gemma-3",
    "internvl",
    "molmo",
    "phi-3.5-vision",
    "phi-4-multimodal",
)

#: Refuse to inline anything larger than this. Base64 inflates by ~33%, and a
#: gigapixel-slide crop that escaped resizing would otherwise be sent whole.
MAX_IMAGE_BYTES = 6 * 1024 * 1024

_DEFAULT_SYSTEM_PROMPT = (
    "You are a spatial transcriptomics research assistant. You interpret "
    "regions a researcher selects on a tissue slide, using differential "
    "expression, gene annotations, pathway enrichment and retrieved "
    "literature. You are precise about what the evidence does and does not "
    "show, and you never present an interpretation as a clinical diagnosis."
)


def model_supports_vision(model_name: str | None, config: object | None = None) -> bool:
    """Return whether ``model_name`` can accept image input.

    An explicit ``deepinfra.vision`` (or ``vision``) config flag always wins;
    the name heuristic is only a fallback, because model naming is not a
    reliable capability signal. Note the app's existing check is a bare
    ``"vl" in model_name`` substring test (worker.py:113), which this
    deliberately widens without contradicting.

    Args:
        model_name: The configured model identifier.
        config: Application config, checked for an explicit override.

    Returns:
        True when the model should be sent image content.
    """

    override = _config_value(config, "deepinfra", "vision")
    if override is None:
        override = _config_value(config, "copilot_agent", "vision")
    if override is not None:
        if isinstance(override, bool):
            return override
        return str(override).strip().lower() in {"1", "true", "yes", "on"}

    name = (model_name or "").lower()
    if not name:
        return False
    return any(marker in name for marker in _VISION_MARKERS)


def _config_value(config: object | None, section: str, key: str) -> Any:
    """Read ``config[section][key]``, tolerating flat dicts and missing keys."""

    if not isinstance(config, dict):
        return None
    block = config.get(section)
    if isinstance(block, dict) and key in block:
        return block[key]
    return config.get(key) if key in config else None


def encode_image_data_uri(image_path: str) -> str:
    """Return ``image_path`` as a base64 ``data:`` URI, or "" if unusable.

    Returns "" rather than raising for every failure mode — a missing or
    oversized crop must degrade to a text-only answer, not lose the turn.
    """

    if not image_path or not os.path.isfile(image_path):
        return ""

    try:
        size = os.path.getsize(image_path)
        if size <= 0 or size > MAX_IMAGE_BYTES:
            return ""
        with open(image_path, "rb") as handle:
            payload = handle.read()
    except OSError:
        return ""

    mime = mimetypes.guess_type(image_path)[0] or "image/png"
    if not mime.startswith("image/"):
        return ""

    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def build_multimodal_prompt_payload(
    question: str,
    roi_image: Any = None,
    deg: Any = None,
    gene_annotations: Any = None,
    pathways: Any = None,
    pubmed: Any = None,
    config: dict | None = None,
    *,
    label: str = "selection",
    roi: Any = None,
    model: str | None = None,
    system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
    evidence_gaps: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build the chat payload for one agent turn.

    Args:
        question: The researcher's message.
        roi_image: ``ROIImageResult``-like object carrying ``crop_path``.
        deg: DEG payload in any supported shape.
        gene_annotations: ``GeneAnnotationResult`` or ``None``.
        pathways: ``PathwayResult`` or ``None``.
        pubmed: ``PubMedResult`` or ``None``.
        config: Application config; supplies the model name and vision flag.
        label: Region label.
        roi: ``ROISelection``-like object.
        model: Model identifier override.
        system_prompt: System framing.
        evidence_gaps: Notes about evidence that was unavailable.

    Returns:
        A dict with ``messages`` (OpenAI chat schema), ``model``,
        ``image_included``, ``image_path`` and ``text_prompt``. When the model
        has no vision support, or the crop is missing, ``messages`` carries
        plain string content and ``image_included`` is False.
    """

    model_name = model or _config_value(config, "deepinfra", "model") or ""
    crop_path = adapters.clean_text(adapters.get_field(roi_image, "crop_path"))

    supports_vision = model_supports_vision(model_name, config)
    data_uri = encode_image_data_uri(crop_path) if supports_vision else ""
    image_included = bool(data_uri)

    evidence = build_evidence_context(
        label=label,
        gene_objects=adapters.normalize_gene_objects(deg),
        annotation_result=gene_annotations,
        pathway_result=pathways,
        pubmed_result=pubmed,
        roi=roi,
        roi_image=roi_image,
        image_attached=image_included,
        evidence_gaps=evidence_gaps,
    )

    text_prompt = f"{question.strip()}{evidence}" if question else evidence.lstrip("\n")

    if image_included:
        user_content: Any = [
            {"type": "text", "text": text_prompt},
            {"type": "image_url", "image_url": {"url": data_uri}},
        ]
    else:
        user_content = text_prompt

    return {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "image_included": image_included,
        # The path is reported even when the image was not sent, so callers can
        # tell "no crop existed" apart from "the model cannot see".
        "image_path": crop_path,
        "text_prompt": text_prompt,
    }


__all__ = [
    "MAX_IMAGE_BYTES",
    "build_multimodal_prompt_payload",
    "encode_image_data_uri",
    "model_supports_vision",
]
