# Functional Specifications: Spatial Omics Copilot

## 1. End-to-End Behavior

1. Researcher opens the app and uploads a whole-slide image (TIFF/OME-TIFF).
2. App converts the image to a pyramidal OME-TIFF and renders it in VivViewer.
3. Researcher optionally uploads an h5ad file; app runs spatial clustering and overlays colored spots.
4. Researcher clicks a cluster or draws an ROI polygon on the tissue.
5. App runs DEG automatically and caches the gene list (`cluster_context.json` or `roi_context.json`).
6. When a chat message is sent, `routes.py` calls `run_copilot_agent(question, deg, label, disease, ...)` directly (the extensible entry point, not the frozen `run_agent()` — see section 3.4).
7. Agent decides whether to call gene_annotation_tool, pathway_tool, and/or pubmed_tool based on the question; results are formatted into a context string injected into the LLM prompt.
8. LLM response streams token by token to the chat interface.
9. Chat UI shows AGENT TRACE card, pathway bar chart, DEG bar chart, then streamed text.
10. Researcher can ask follow-up questions; agent continues with full region context.

## 2. Data Input Contract

| **Input** | **Format** | **Required Behavior** |
| --- | --- | --- |
| Whole-slide image | .tiff, .ome.tiff, .svs | Convert to pyramidal OME-TIFF; render via VivViewer |
| Gene expression | .h5ad | Validate `adata.obsm["spatial"]`; run clustering; overlay spots |
| Cluster selection | cluster label string | `app.py` calls DEG immediately; gene list cached to `cluster_context.json` |
| ROI polygon | list of coordinate lists | `app.py` calls DEG immediately; gene list cached to `roi_context.json` |

## 3. RAG Tool Contracts

### 3.1 DEG Extraction

```python
# rag/deg/__init__.py
def get_cluster_high_expression_genes(work_dir, cluster_id, folder_id="", top_n=25) -> dict
def get_roi_high_expression_genes(work_dir, coords, folder_id="", top_n=25) -> dict
```

Returns:
```python
{
    "selected_spots":  120,
    "reference_spots": 880,
    "total_spots":     1000,
    "ranking_method":  "roi_vs_non_roi_log2fc",
    "top_genes": [
        {
            "gene": "SNAP25",
            "log2_fold_change": 3.81,
            "mean_expression": 2.4,
            "pct_spots_expressed": 0.87,
            "mean_reference": 0.3,
            "pct_reference": 0.12,
            "pvalue": 1.2e-5,        # Mann-Whitney U, two-sided (T-008)
            "adj_pvalue": 3.4e-4,    # Benjamini-Hochberg corrected (T-009)
            "statistic": 91234.0,
            "testable": True,
            "untestable_reason": "",
        },
        ...
    ]
}
```

Required behavior:
- Read h5ad path from `spatial_omics.json` in `work_dir`.
- Return `None` if h5ad is not loaded or selection has no spots.
- Rank by log2 fold-change (selected vs reference spots); `pvalue`/`adj_pvalue` come from a Wilcoxon rank-sum test with Benjamini-Hochberg correction, pre-filtering low-count genes first for performance.
- `rag.contracts.DEGResult`/`GeneStat` are the canonical typed form of this dict; `rag.deg.models` re-exports them for backward compatibility.

### 3.2 Pathway Enrichment

```python
# rag/pathway_enrichment/__init__.py  (rag/pathway/ is a back-compat import path)
def run_pathway_enrichment(genes: list[str], config: dict | None = None) -> PathwayResult
```

Returns:
```python
[
    {
        "name":       "GO:0007268 · Chemical synaptic transmission",
        "gene_count": 8,
        "set_size":   21,
        "pvalue":     1.2e-5,
        "overlap":    ["SNAP25", "SYP", ...],
    },
    ...
]
```

Required behavior:
- Run real ORA against GO Biological Process and KEGG via Enrichr's HTTPS API.
- Sort by ascending adjusted p-value.
- Return an empty result when no pathways are enriched, or when Enrichr is unreachable — the two cases have different `status_message` text (the latter contains "unavailable") so callers can tell a real failure apart from a genuine negative result instead of treating both as one clean "no results" checkmark.
- Submit the gene list once, fetch each configured library (GO, KEGG) separately, and merge the results. Retry transient HTTP failures and fall back from Enrichr's tabular export endpoint to its JSON enrichment endpoint.
- `rag.pathway_enrichment.models.PathwayEntry`/`PathwayResult` are the typed result; `overlap`/`gene_count`/`set_size`/`pvalue` are legacy dict-style aliases still supported for existing callers.

### 3.3 PubMed Retrieval

```python
# rag/pubmed_retrieval/__init__.py
def search_pubmed(query: str, max_results: int = 5, ...) -> PubMedResult
def build_pubmed_query(genes: list[str], pathways: list[str] | None, disease: str = "colorectal cancer") -> str
```

Returns:
```python
[
    {
        "pmid":    "38912204",
        "title":   "Spatial transcriptomics reveals...",
        "journal": "Nature Cancer",
        "year":    2024,
        "snippet": "Spatial analysis identified...",
    },
    ...
]
```

Required behavior:
- Build query from gene symbols, pathway names, and a disease anchor.
- Call NCBI E-utilities (esearch + efetch).
- Return up to `n` relevant results. If fewer relevant results are found, return fewer results instead of padding with unrelated papers.
- Respect rate limits (3 req/s without key, 10 req/s with `PUBMED_API_KEY`).
- The disease anchor matters more than it looks: a wrong value does not fail loudly, it returns confident, well-formed papers about the wrong disease. `app/routes.py` extracts the disease/sample context from the conversation once per session (cached after the first success) and passes it through; if nothing has been stated yet, this still falls back to a fixed default (`"colorectal cancer"`) rather than skipping the anchor — see `docs/tech.md` section 8 for this as an open risk.

### 3.4 Agent Entry Point

```python
# rag/agent/__init__.py — re-exported from rag/copilot_agent, the real implementation
def run_agent(gene_objects, message="", label="selection") -> dict
```

This signature is frozen and must not change — it is a thin wrapper around the extensible entry point below, with no `disease` parameter. `app/routes.py` calls the extensible one directly instead so the extracted disease context can actually reach it:

```python
def run_copilot_agent(
    question="", roi=None, roi_image=None, deg=None,
    gene_annotations=None, pathways=None, pubmed=None, config=None, *,
    label="selection", image_attached=None, max_tool_calls=5,
    semantic_rerank=False, disease=None, synthesize_answer=False,
) -> AgentResult   # call .to_legacy_dict() for the same dict shape as run_agent()
```

`roi_image`/`image_attached` carry the cropped ROI image and whether it actually reached the model — `run_agent()` has no equivalent parameters, so on that path the evidence block states visual claims as conditional rather than asserting either way.

**Input parameters (`run_agent`):**

| Parameter | Type | Description |
| --- | --- | --- |
| `gene_objects` | `list[dict]` | Pre-computed DEG result from `app.py`. Each dict has `"gene"` (str) and `"log2_fold_change"` (float). Pass `[]` only when no valid DEG context is available. DEG is **not** run inside `run_agent` — it runs automatically in `app.py` when the user selects a cluster or ROI. |
| `message` | `str` | User's chat message. The real agent uses this to decide which tools to call and to build smarter PubMed queries. |
| `label` | `str` | Human-readable region label for UI headers, e.g. `"Cluster 5"`, `"ROI"`, `"demo"`. |

Fallback: if `gene_objects` is empty, the system must clearly state that no ROI-specific gene expression context is available. Demo genes may be used only in an explicitly labeled demo mode, never as real ROI analysis.

**Output:**

```python
{
    "gene_objects": [
        {"gene": str, "log2_fold_change": float},
        ...
    ],
    # Full DEG list — used by app.py to render the cluster gene popup card.

    "context_str": str,
    # Formatted evidence string prepended to the LLM prompt by worker.py.
    # Must start with "\n\n". Contains genes, pathways, and abstract snippets.

    "metadata": {
        "trace": [
            {"step": str, "detail": str, "icon": str},
            ...
        ],
        # Steps the agent actually ran — shown in the AGENT TRACE card in the UI.
        # icon values: "deg", "pathway", "pubmed"

        "degs": [{"gene": str, "log2fc": float}, ...],
        # Top 8 DEGs — shown as a bar chart in the chat UI.

        "pathways": [
            {"source": str, "name": str, "neg_log10p": float, "gene_count": int},
            ...
        ],
        # Enriched pathways — shown as a bar chart in the chat UI.
        # source: "GO" or "KEGG"   neg_log10p: bar length   gene_count: label

        "citations": [
            {"id": int, "pmid": str, "title": str, "journal": str, "year": int},
            ...
        ],
        # PubMed abstracts — shown as citation list in the chat UI.
        # id: 1-based index used for inline citation like [1], [2]

        "label": str,
        # Human-readable region label shown in panel headers.
        # e.g. "Cluster 5", "ROI", "demo"
    }
}
```

## 4. Agent Behavior

**DEG extraction is not the agent's decision.** It runs automatically when the user clicks a cluster or draws an ROI in the UI (`app.py`), before any chat message is sent. The gene list is already available by the time the agent runs.

The LangGraph agent in `src/rag/copilot_agent/graph.py` (`src/rag/agent/` is a back-compat import path, not the implementation):
1. Receives the pre-computed gene list (from DEG) along with the user message.
2. Decides whether to call **gene_annotation_tool** (NCBI Gene functional summaries) based on the question.
3. Decides whether to call **pathway_tool** (GO / KEGG enrichment) based on the question.
4. Decides whether to call **pubmed_tool** (NCBI abstract retrieval) based on the question.
5. Passes all results, plus the disease/sample context extracted from the conversation (when known), to `prompt.py` to build `context_str`.
6. Returns the structured result dict above.

Required behavior:
- `trace` must reflect what the agent actually ran — not a hardcoded list. DEG appears when valid DEG context exists; otherwise the trace should clearly show that DEG context is unavailable.
- Agent decides gene annotation, pathway, and/or PubMed based on the user message — any combination, including none.
- Must not invent gene functions, pathway names, or citations.
- A tool's connection failure must be distinguishable from it genuinely finding nothing — both used to collapse into the same "empty" status, silently presenting a network/API failure as if it were a real negative result.
- If no tools return results, answer only from the available ROI/gene context and clearly state which evidence sources were unavailable.
- Limit to 5 tool calls per turn to prevent infinite loops.
- When a vision-capable model is selected, `app/worker.py` attaches the cropped ROI image to the same message carrying `context_str`; the agent does not know for certain whether the image actually reached the model (its own signature carries no image argument), so the prompt instructs the model to describe tissue appearance only if an image is genuinely attached, rather than asserting either way.
- The disease/sample context is stated explicitly in the evidence block only when it was actually extracted from the conversation — never a guessed default — since asserting an unverified guess risks the same wrong-sample failure this exists to prevent.

## 5. Chat Interface Behavior

- One session per workspace; stored in `data/chat_sessions/<session_id>/session.json`.
- LLM responses stream token by token.
- Chat UI renders in order: AGENT TRACE → PATHWAY panel → DEG panel → LLM text.
- ROI thumbnails attach to the relevant assistant message.
- Session persists across page reloads; cleared only on Reset.

## 6. Upload Behavior

- Whole-slide images processed asynchronously with progress bar.
- h5ad files validated for `adata.obsm["spatial"]` before processing.
- After h5ad upload, spatial clustering runs automatically.
- Spot overlay appears after clicking Re-visualize Image.

## 7. Edge Cases

| **Edge Case** | **Expected Behavior** |
| --- | --- |
| ROI contains no spots | Return empty gene list; explain that no spatial expression spots were found in the ROI |
| Pathway API unavailable | Return empty list; agent answers from gene context only |
| PubMed returns no results | Return empty list; inform user |
| LLM unavailable | Show error message; do not stream partial answer |
| h5ad missing spatial coordinates | Reject with clear error on upload |
| No h5ad loaded | Show a clear “No gene expression data loaded” message; do not present demo genes as ROI analysis |
| Image too large for memory | Use pyvips streaming |
| Session file corrupted | Start fresh session; log error |
| ROI selection erased | Clear the cached gene list and cropped ROI image too, not just the raw coordinates — otherwise chat keeps silently answering about the previous selection |
| Tool call fails to connect (network/API error) | Report it as an error, distinct from a genuine empty result — both used to look like the same clean "no results" checkmark |
| Disease/sample context never stated in conversation | The LLM's evidence block stays silent on tissue identity rather than asserting a guess; PubMed's query still falls back to a fixed default disease anchor (open risk, see `docs/tech.md` section 8) |
