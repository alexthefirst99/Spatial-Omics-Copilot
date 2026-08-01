"""Result models for the copilot agent.

PROVISIONAL — ``AgentResult`` here is a local stand-in for the shared result
contract. Person 6 owns ``src/rag/contracts.py``; once ``contracts.AgentResult``
lands, this class should become a thin adapter over the shared type. Nothing
outside ``rag.copilot_agent`` should depend on these classes directly — depend
on :meth:`AgentResult.to_legacy_dict`, which is the stable surface. This mirrors
the pattern Person 2 used in ``rag.deg.models``.

Two output shapes matter:

``to_legacy_dict()``
    The exact ``{gene_objects, context_str, metadata}`` dict that
    ``app/routes.py`` reads and ``app/assets/chat.js`` renders. This is a hard
    contract — see ``docs/specs.md`` section 3.4.

``to_dict()``
    The richer plan-level view (``answer``, ``trace``, ``citations``,
    ``used_roi_image``) that ``run_copilot_agent`` returns for the integration
    pipeline.

Numeric safety is not optional here. ``chat.js`` calls ``.toFixed(1)`` directly
on ``pathways[].neg_log10p`` (chat.js:272) and ``degs[].log2fc`` (chat.js:330).
A ``None``, a missing key, or a *string* number raises a TypeError inside
``aiRespond``'s try block, which aborts before the polling loop starts — the
user sees an error and the LLM answer for that turn is lost entirely. Every
numeric field below is therefore coerced through :func:`_as_float` /
:func:`_as_int`, which cannot emit ``None``, ``NaN`` or an infinity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Any

# Trace icons. app/assets/chat.js never reads trace[].icon — every row gets a
# hardcoded green check — so these are advisory metadata for future UI work and
# for the validation notes. Adding a new value cannot break rendering.
ICON_DEG = "deg"
ICON_PATHWAY = "pathway"
ICON_PUBMED = "pubmed"
ICON_GENE = "gene"
ICON_IMAGE = "image"
ICON_AGENT = "agent"

# Tool-call outcomes recorded in the trace (T-022).
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_ERROR = "error"
STATUS_SKIPPED = "skipped"


def _as_text(value: object) -> str:
    """Return a clean single-line string, never ``None``.

    An absent key renders as the literal text ``"undefined"`` in the browser
    (``textContent`` coerces ``undefined`` via ToString), so emitting ``""`` is
    always preferable to omitting a string field.
    """

    if value is None:
        return ""
    return " ".join(str(value).split())


def _as_float(value: object, default: float = 0.0) -> float:
    """Coerce to a finite float. Never returns ``None``, ``NaN`` or infinity."""

    if isinstance(value, bool) or value is None:
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _as_int(value: object, default: int = 0) -> int:
    """Coerce to an int, falling back to ``default`` on anything unusable."""

    if isinstance(value, bool) or value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class TraceStep:
    """One recorded agent action (T-022).

    ``step`` and ``detail`` are what the UI renders; ``tool``, ``status``,
    ``input_summary`` and ``output_summary`` satisfy the ticket's requirement to
    record what each tool was called with and what it returned. The extra keys
    are inert in the current front-end, which reads only ``step`` and ``detail``.

    Attributes:
        step: Row text in the AGENT TRACE card.
        detail: Monospace grey suffix. Omitted by the UI when empty.
        icon: Advisory category; not read by the current front-end.
        tool: Tool name, or "" for non-tool steps such as routing.
        status: One of ``STATUS_OK`` / ``STATUS_EMPTY`` / ``STATUS_ERROR`` /
            ``STATUS_SKIPPED``.
        input_summary: Short description of what the tool was called with.
        output_summary: Short description of what came back.
    """

    step: str
    detail: str = ""
    icon: str = ICON_AGENT
    tool: str = ""
    status: str = STATUS_OK
    input_summary: str = ""
    output_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return the trace row. Keys ``step``/``detail``/``icon`` are the UI contract."""

        return {
            "step": _as_text(self.step),
            "detail": _as_text(self.detail),
            "icon": _as_text(self.icon) or ICON_AGENT,
            "tool": _as_text(self.tool),
            "status": _as_text(self.status) or STATUS_OK,
            "input_summary": _as_text(self.input_summary),
            "output_summary": _as_text(self.output_summary),
        }


@dataclass(frozen=True, slots=True)
class Citation:
    """A PubMed record the agent actually retrieved this turn.

    ``docs/rules.md`` section 4 permits citing only PMIDs returned by the PubMed
    tool in the same turn, so this list is built from tool output and never from
    model text.
    """

    id: int
    pmid: str = ""
    title: str = ""
    journal: str = ""
    year: int | None = None

    @property
    def url(self) -> str:
        """Canonical PubMed URL, or "" when there is no PMID."""

        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/" if self.pmid else ""

    def to_dict(self) -> dict[str, Any]:
        """Return the citation chip payload.

        The chip template is ``[${c.id}] ${c.journal} · PMID ${c.pmid}``
        (chat.js:344), so ``journal`` and ``pmid`` must be strings — an absent
        key would print the literal "undefined".
        """

        return {
            "id": _as_int(self.id),
            "pmid": _as_text(self.pmid),
            "title": _as_text(self.title),
            "journal": _as_text(self.journal),
            "year": self.year if isinstance(self.year, int) else None,
        }


@dataclass(frozen=True, slots=True)
class PathwayBar:
    """One row of the ENRICHED PATHWAYS panel.

    Attributes:
        source: Short tag rendered as a pill, e.g. "GO" or "KEGG". Must be a
            string — a truthy non-string raises ``p.source.replace is not a
            function`` at chat.js:254. Empty string is the safe "no tag" value.
        name: Row label and hover tooltip.
        neg_log10p: Bar length, printed via ``.toFixed(1)``. Must be a number.
        gene_count: Overlap size. Not currently read by the front-end.
    """

    source: str
    name: str
    neg_log10p: float
    gene_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": _as_text(self.source),
            "name": _as_text(self.name),
            "neg_log10p": round(_as_float(self.neg_log10p), 1),
            "gene_count": _as_int(self.gene_count),
        }


@dataclass(frozen=True, slots=True)
class DegBar:
    """One row of the TOP DEGs panel.

    ``log2fc`` drives the bar and is printed with ``.toFixed(1)``; it must be a
    number for the same reason as :attr:`PathwayBar.neg_log10p`.
    """

    gene: str
    log2fc: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "gene": _as_text(self.gene),
            "log2fc": round(_as_float(self.log2fc), 2),
        }


@dataclass(slots=True)
class AgentResult:
    """Everything one agent turn produced.

    Attributes:
        answer: Synthesized answer text. Empty when the LLM call is delegated to
            ``app/worker.py``, which is the default — ``src/rag/`` does not own
            the streaming LLM call (``docs/rules.md`` section 3).
        trace: Ordered record of what the agent actually did (T-022).
        citations: PubMed records retrieved this turn.
        used_roi_image: Whether the cropped ROI image was part of the model
            input. Named in Person 6's suggested ``AgentResult`` contract.
        context_str: Evidence block appended to the user prompt by
            ``app/worker.py``. MUST begin with "\\n\\n" — it is raw-concatenated
            onto the end of the prompt (worker.py:159).
        gene_objects: The DEG list echoed back, unchanged.
        degs: Bar-chart rows for the TOP DEGs panel.
        pathways: Bar-chart rows for the ENRICHED PATHWAYS panel.
        label: Region label for panel headers, e.g. "Cluster 5" or "ROI".
        intent: Routing intent label from ``copilot_agent.routing``.
        tools_called: Tool names actually invoked, in call order.
        status_message: Human-readable state, surfaced when evidence is missing.
    """

    answer: str = ""
    trace: list[TraceStep] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    used_roi_image: bool = False
    context_str: str = ""
    gene_objects: list[dict] = field(default_factory=list)
    degs: list[DegBar] = field(default_factory=list)
    pathways: list[PathwayBar] = field(default_factory=list)
    label: str = "selection"
    intent: str = ""
    tools_called: list[str] = field(default_factory=list)
    status_message: str = ""

    @property
    def ok(self) -> bool:
        """Whether the turn produced any evidence at all."""

        return bool(self.degs or self.pathways or self.citations)

    def to_metadata(self) -> dict[str, Any]:
        """Return the ``metadata`` block consumed by chat.js.

        Note the citation coupling: ``buildDegPanel`` returns early when
        ``degs`` is empty (chat.js:284) and the citation chips are appended
        inside that same panel, so citations are only *visible* when at least
        one DEG row exists. The data is emitted regardless — the agent trace
        also names the retrieved PMIDs so the evidence is never invisible.
        """

        return {
            "trace": [step.to_dict() for step in self.trace],
            "degs": [bar.to_dict() for bar in self.degs],
            "pathways": [bar.to_dict() for bar in self.pathways],
            "citations": [citation.to_dict() for citation in self.citations],
            "label": _as_text(self.label) or "selection",
            # Extra keys are permitted (docs/rules.md section 4) and ignored by
            # the current front-end.
            "intent": _as_text(self.intent),
            "tools_called": list(self.tools_called),
            "used_roi_image": bool(self.used_roi_image),
            "status_message": _as_text(self.status_message),
        }

    def to_legacy_dict(self) -> dict[str, Any]:
        """Return the exact dict ``app/routes.py`` expects.

        ``routes.py`` uses bracket access on ``context_str`` and ``metadata``
        (routes.py:487-488); omitting either raises a KeyError inside its try
        block, which silently drops both the LLM context and the UI panels for
        that turn. All three keys are therefore always present.
        """

        context = self.context_str or ""
        if context and not context.startswith("\n\n"):
            context = "\n\n" + context.lstrip("\n")

        return {
            "gene_objects": list(self.gene_objects),
            "context_str": context,
            "metadata": self.to_metadata(),
        }

    def to_dict(self) -> dict[str, Any]:
        """Return the richer plan-level view used by the integration pipeline."""

        return {
            "answer": self.answer,
            "trace": [step.to_dict() for step in self.trace],
            "citations": [citation.to_dict() for citation in self.citations],
            "used_roi_image": bool(self.used_roi_image),
            "context_str": self.context_str,
            "intent": _as_text(self.intent),
            "tools_called": list(self.tools_called),
            "label": _as_text(self.label) or "selection",
            "status_message": _as_text(self.status_message),
        }


__all__ = [
    "ICON_AGENT",
    "ICON_DEG",
    "ICON_GENE",
    "ICON_IMAGE",
    "ICON_PATHWAY",
    "ICON_PUBMED",
    "STATUS_EMPTY",
    "STATUS_ERROR",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "AgentResult",
    "Citation",
    "DegBar",
    "PathwayBar",
    "TraceStep",
]
