"""Rule-based question routing for the copilot agent (T-021).

The agent decides which evidence tools to call from the user's question alone.
Routing is deliberately deterministic rather than LLM-driven:

* ``docs/rules.md`` section 4 caps the agent at five tool calls per turn, and a
  deterministic router cannot spend that budget on a mis-parsed instruction.
* Every tool in this project performs network I/O (Enrichr, NCBI Gene, PubMed).
  A router that is reproducible in tests is worth more than one that is clever,
  because a wrong route costs the user seconds, not tokens.
* ``tests/test_agent.py`` must be able to assert routing decisions without a
  live model.

The router never reads gene expression data. It answers one question — *which
evidence would a domain expert reach for, given this sentence?* — and leaves
availability checks to :func:`plan_tools`.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

# Intent labels. These are stable strings: they appear in the agent trace and
# in tests, so renaming one is a contract change.
INTENT_PATHWAY = "pathway"
INTENT_GENE_FUNCTION = "gene_function"
INTENT_LITERATURE = "literature"
INTENT_EXPLANATION = "biological_explanation"
INTENT_IMAGE = "image_pattern"
INTENT_SUMMARY = "roi_summary"
INTENT_GENERAL = "general_chat"

# Tool names. These match the callables exposed by ``copilot_agent.tools`` and
# are what ``metadata.trace`` reports, so they are also a contract.
TOOL_PATHWAY = "pathway_tool"
TOOL_GENE_ANNOTATION = "gene_annotation_tool"
TOOL_PUBMED = "pubmed_tool"

ALL_TOOLS = (TOOL_GENE_ANNOTATION, TOOL_PATHWAY, TOOL_PUBMED)

#: Gene symbols this long or shorter must match the question's case exactly.
#: Longer symbols (EPCAM, CEACAM5) are unambiguous enough to match loosely.
_SHORT_SYMBOL_LEN = 4


def _terms(*words: str) -> tuple[re.Pattern[str], ...]:
    """Compile strict whole-word patterns.

    Multi-word entries are matched as phrases with flexible internal
    whitespace.

    Both boundaries are anchored, so an entry matches only the exact word.
    An earlier version appended ``\\w*`` to cover inflections cheaply, which
    silently turned every entry into a prefix match — ``"color"`` then matched
    **"colorectal"**, routing questions about this project's own demo tissue to
    the image branch and running no evidence tools at all, and ``"gene"``
    matched ``"general"`` and ``"generate"``. Inflections are listed explicitly
    below instead; that is more typing and far fewer surprises.
    """

    patterns: list[re.Pattern[str]] = []
    for word in words:
        escaped = r"\s+".join(re.escape(part) for part in word.split())
        patterns.append(re.compile(rf"\b{escaped}\b", re.IGNORECASE))
    return tuple(patterns)


# --- Signal vocabularies -------------------------------------------------
#
# Each vocabulary is scored independently, so "which pathways, and any papers?"
# routes to two tools rather than forcing a single winner.

_PATHWAY_TERMS = _terms(
    "pathway", "pathways", "enrich", "enriched", "enriches", "enrichment",
    "ora", "gsea", "gene ontology", "go term", "go terms", "kegg", "reactome",
    "signaling", "signalling", "signal transduction", "biological process",
    "biological processes", "molecular function", "cellular component",
    "gene set", "gene sets", "over representation", "overrepresentation",
)

# Deliberately excludes bare interrogatives such as "what does" — those match
# any question, including "what does this region look like?", and would starve
# the image route of its own questions.
_GENE_FUNCTION_TERMS = _terms(
    "gene", "genes", "marker", "markers", "deg", "degs",
    "differentially expressed", "differential expression",
    "upregulated", "up regulated", "downregulated", "down regulated",
    "overexpressed", "expression", "expressed", "transcript", "transcripts",
    "function", "functions", "functional",
    "encode", "encodes", "protein", "proteins", "annotation", "annotations",
    "alias", "aliases", "symbol", "symbols", "fold change", "log2",
)

_LITERATURE_TERMS = _terms(
    "paper", "papers", "publication", "publications", "literature",
    "pubmed", "pmid", "pmids", "citation", "citations", "cite", "reference",
    "references", "study", "studies", "article", "articles", "evidence",
    "published", "journal", "author", "authors", "review", "reviews",
    "prior work", "known from", "reported", "abstract", "abstracts",
)

# Strictly visual cues only. Words that merely *co-occur* with tissue talk —
# "structure", "architecture", "pattern", "stroma", "colour" — were removed:
# they made "explain the biology of the stromal compartment" an image question
# (zero evidence tools), and "colour" collided with "colorectal" outright.
# A term earns a place here only if a researcher using it is asking what the
# tissue LOOKS LIKE.
_IMAGE_TERMS = _terms(
    "image", "images", "picture", "photo", "crop", "cropped", "thumbnail",
    "morphology", "morphological", "histology", "histological",
    "h&e", "he stain", "stain", "stained", "staining",
    "tissue architecture", "tissue pattern", "spatial pattern", "texture",
    "look", "looks", "looking", "see", "seen", "visible", "visually", "visual",
    "appearance", "microscope", "magnification",
    "gland", "glands", "glandular", "nuclei", "nucleus", "cell shape",
    "necrosis", "necrotic", "invasive front",
)

# Broad interpretive asks that justify pulling every evidence source.
_SUMMARY_TERMS = _terms(
    "summary", "summarise", "summarize", "overview", "interpret",
    "interpretation", "explain", "explanation", "describe", "description",
    "characterise", "characterize", "characterisation", "characterization",
    "what is this", "what is happening", "what's happening", "whats happening",
    "what is going on", "what's going on", "tell me about", "walk me through",
    "analyse", "analyze", "analysis", "insight", "insights",
    "cell type", "cell types", "celltype", "phenotype", "tumor", "tumour",
    "tumors", "tumours", "cancer", "disease", "biology", "biological",
    "clinical", "prognosis", "prognostic", "significance", "meaning",
    "why", "how come",
    # Disease and tissue vocabulary. A question naming the tissue or a
    # diagnosis is a biological question even with no other cue —
    # "Is this region colorectal adenocarcinoma?" must gather evidence.
    "colorectal", "colon", "rectal", "adenocarcinoma", "carcinoma",
    "neoplasm", "malignant", "malignancy", "benign", "dysplasia", "dysplastic",
    "metastasis", "metastatic", "invasion", "invasive",
    "epithelium", "epithelial", "mucosa", "mucosal", "fibroblast",
    "fibroblasts", "macrophage", "macrophages", "lymphocyte", "lymphocytes",
    "immune", "inflammation", "inflammatory", "stroma", "stromal",
    "proliferation", "apoptosis", "angiogenesis", "microenvironment",
    "subtype", "subtypes",
)

# Conversational openers that must not trigger a network call.
_GENERAL_TERMS = _terms(
    "hello", "hi", "hey", "thanks", "thank you", "bye", "goodbye",
    "who are you", "what can you do", "help me use", "how do i use",
    "test", "testing", "ping", "ok", "okay", "nice", "cool", "great",
)


def _hits(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    """Return the distinct matched substrings, preserving discovery order."""

    found: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        match = pattern.search(text)
        if match is None:
            continue
        token = match.group(0).lower()
        if token not in seen:
            seen.add(token)
            found.append(token)
    return found


@dataclass(frozen=True, slots=True)
class ToolPlan:
    """Which tools to run for one turn, and why.

    Attributes:
        intent: One of the ``INTENT_*`` constants.
        tools: Ordered tool names to invoke. Ordering is meaningful — pathway
            names feed the PubMed query, so ``pathway_tool`` precedes
            ``pubmed_tool`` whenever both run.
        reason: Human-readable justification, surfaced in the agent trace so a
            reviewer can see why a tool did or did not run.
        matched_terms: Question tokens that triggered the decision. Kept for
            debugging and for the validation notes; not part of any contract.
        uses_roi_image: Whether the answer should lean on the cropped ROI image.
    """

    intent: str
    tools: tuple[str, ...] = ()
    reason: str = ""
    matched_terms: tuple[str, ...] = ()
    uses_roi_image: bool = False

    def wants(self, tool_name: str) -> bool:
        """Whether ``tool_name`` is scheduled to run this turn."""

        return tool_name in self.tools


@dataclass(frozen=True, slots=True)
class QuestionSignals:
    """Raw keyword evidence extracted from one user message."""

    pathway: list[str] = field(default_factory=list)
    gene_function: list[str] = field(default_factory=list)
    literature: list[str] = field(default_factory=list)
    image: list[str] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    general: list[str] = field(default_factory=list)
    gene_symbols: list[str] = field(default_factory=list)

    @property
    def any_specific(self) -> bool:
        """Whether the user named a concrete evidence type."""

        return bool(
            self.pathway or self.gene_function or self.literature or self.gene_symbols
        )

    @property
    def any_biological(self) -> bool:
        """Whether the message is about the tissue at all.

        Used to keep genuinely off-topic questions ("what is the weather?")
        from triggering three network calls.
        """

        return bool(
            self.pathway
            or self.gene_function
            or self.literature
            or self.image
            or self.summary
            or self.gene_symbols
        )


def _mentioned_genes(text: str, genes: Sequence[str]) -> list[str]:
    """Return DEG gene symbols the user named explicitly.

    Matching against the actual ROI gene list — rather than guessing at
    uppercase tokens — keeps "Is this DNA damage?" from being read as a gene
    mention while still catching "what does EPCAM do?", where no keyword from
    :data:`_GENE_FUNCTION_TERMS` appears at all.
    """

    found: list[str] = []
    seen: set[str] = set()
    for gene in genes or ():
        symbol = str(gene).strip()
        # Two characters is the shortest real HGNC symbol; single characters
        # would match ordinary words.
        if len(symbol) < 2 or symbol.upper() in seen:
            continue

        # CAT, SET, REST, MAX, ACHE, SHE, PIGS and TH are all real HGNC
        # symbols and all ordinary English words. Matching those
        # case-insensitively turns "what is the cat doing here?" into a gene
        # question and fires a live NCBI lookup. A researcher naming a gene
        # writes it in caps, so short symbols require an exact-case match.
        flags = 0 if len(symbol) <= _SHORT_SYMBOL_LEN else re.IGNORECASE
        if re.search(rf"\b{re.escape(symbol)}\b", text, flags):
            seen.add(symbol.upper())
            found.append(symbol.upper())
    return found


def detect_signals(question: str, genes: Sequence[str] = ()) -> QuestionSignals:
    """Extract keyword evidence from ``question``.

    Args:
        question: The raw user message. May be empty.
        genes: ROI gene symbols, used to detect when the user names a specific
            gene without using any generic gene vocabulary.

    Returns:
        A :class:`QuestionSignals` holding the matched tokens per vocabulary.
    """

    text = (question or "").strip()
    if not text:
        return QuestionSignals()

    return QuestionSignals(
        pathway=_hits(text, _PATHWAY_TERMS),
        gene_function=_hits(text, _GENE_FUNCTION_TERMS),
        literature=_hits(text, _LITERATURE_TERMS),
        image=_hits(text, _IMAGE_TERMS),
        summary=_hits(text, _SUMMARY_TERMS),
        general=_hits(text, _GENERAL_TERMS),
        gene_symbols=_mentioned_genes(text, genes),
    )


def classify_question(question: str, genes: Sequence[str] = ()) -> str:
    """Return the dominant ``INTENT_*`` label for ``question``.

    Exposed separately from :func:`plan_tools` because the intent is useful for
    prompt framing even when no tool ends up running (for example when no genes
    are available).
    """

    signals = detect_signals(question, genes)
    text = (question or "").strip()

    # No message at all means the caller wants the default region briefing.
    if not text:
        return INTENT_SUMMARY

    # A specific request wins over a broad one: "explain the pathways" is a
    # pathway question, not a generic summary.
    if signals.pathway:
        return INTENT_PATHWAY
    if signals.literature:
        return INTENT_LITERATURE

    # Image wins whenever the user is asking what the tissue looks like and has
    # not also named a gene or gene concept.
    if signals.image and not (signals.gene_function or signals.gene_symbols):
        return INTENT_IMAGE
    if signals.gene_function or signals.gene_symbols:
        return INTENT_GENE_FUNCTION
    if signals.summary:
        return INTENT_EXPLANATION

    # Nothing tissue-related was recognised. Treat it as conversation rather
    # than spending three network calls on it — docs/tickets.md requires that
    # irrelevant queries skip the pathway and PubMed tools.
    return INTENT_GENERAL


def plan_tools(
    question: str,
    *,
    genes: Sequence[str] = (),
    has_genes: bool | None = None,
    has_roi_image: bool = False,
) -> ToolPlan:
    """Decide which evidence tools this question wants.

    This answers *what evidence would help*, not *what will actually run*. The
    per-turn tool budget (``docs/rules.md`` section 4) is applied by the graph,
    which also knows what the caller already supplied — enforcing it here too
    would truncate the plan before the graph could report the omission, leaving
    the user with a trace that silently under-reports what was skipped.

    Args:
        question: The user's chat message.
        genes: ROI gene symbols from the pre-computed DEG result. Used both to
            detect explicit gene mentions and, by default, to decide whether
            any gene-consuming tool can run at all.
        has_genes: Overrides the ``bool(genes)`` default. Every tool in this
            project takes gene symbols as input, so when this is False no tool
            can produce meaningful evidence and none are scheduled.
        has_roi_image: Whether a cropped ROI image is available for the model.

    Returns:
        A :class:`ToolPlan`. The plan is always valid — an empty ``tools``
        tuple is a legitimate outcome, not an error.
    """

    if has_genes is None:
        has_genes = bool(genes)

    signals = detect_signals(question, genes)
    intent = classify_question(question, genes)
    matched = tuple(
        signals.gene_symbols
        + signals.pathway
        + signals.gene_function
        + signals.literature
        + signals.image
        + signals.summary
    )

    # The crop is useful context for any tissue-related question, not only for
    # questions that name the image explicitly.
    uses_image = has_roi_image and intent != INTENT_GENERAL

    # An image question is answered from the crop plus the context already in
    # hand; it needs no external lookup.
    if intent == INTENT_IMAGE:
        return ToolPlan(
            intent=intent,
            tools=(),
            reason=(
                "Tissue-appearance question — answered from the cropped ROI image "
                "and the gene context already loaded; no external lookup needed."
            ),
            matched_terms=matched,
            uses_roi_image=uses_image,
        )

    if intent == INTENT_GENERAL:
        return ToolPlan(
            intent=intent,
            tools=(),
            reason="General conversational message — no evidence lookup required.",
            matched_terms=matched,
            uses_roi_image=False,
        )

    # Past this point every candidate tool consumes gene symbols.
    if not has_genes:
        return ToolPlan(
            intent=intent,
            tools=(),
            reason=(
                "No gene expression data loaded, so pathway, gene annotation and "
                "PubMed lookups have no input genes to work from."
            ),
            matched_terms=matched,
            uses_roi_image=uses_image,
        )

    selected: list[str] = []

    if signals.any_specific:
        # Honour exactly what was asked for. Order matters: annotations and
        # pathway names both feed the PubMed query built later in the turn.
        if signals.gene_function or signals.gene_symbols:
            selected.append(TOOL_GENE_ANNOTATION)
        if signals.pathway:
            selected.append(TOOL_PATHWAY)
        if signals.literature:
            selected.append(TOOL_PUBMED)
        reason = f"Question explicitly asks about {_describe(selected)}."
    else:
        # Broad interpretive ask — gather every evidence source.
        selected = list(ALL_TOOLS)
        reason = (
            "Open-ended interpretation request — gathering gene annotations, "
            "pathway enrichment and literature evidence."
        )

    return ToolPlan(
        intent=intent,
        tools=tuple(selected),
        reason=reason,
        matched_terms=matched,
        uses_roi_image=uses_image,
    )


_TOOL_DESCRIPTIONS = {
    TOOL_GENE_ANNOTATION: "gene function",
    TOOL_PATHWAY: "pathways",
    TOOL_PUBMED: "literature",
}


def _describe(tools: list[str]) -> str:
    """Render a tool list as prose for the trace, e.g. ``"pathways and literature"``."""

    labels = [_TOOL_DESCRIPTIONS.get(tool, tool) for tool in tools]
    if not labels:
        return "nothing"
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " and " + labels[-1]


__all__ = [
    "ALL_TOOLS",
    "INTENT_EXPLANATION",
    "INTENT_GENERAL",
    "INTENT_GENE_FUNCTION",
    "INTENT_IMAGE",
    "INTENT_LITERATURE",
    "INTENT_PATHWAY",
    "INTENT_SUMMARY",
    "QuestionSignals",
    "TOOL_GENE_ANNOTATION",
    "TOOL_PATHWAY",
    "TOOL_PUBMED",
    "ToolPlan",
    "classify_question",
    "detect_signals",
    "plan_tools",
]
