# Functional Specifications: Spatial Omics Copilot

## 1. End-to-End Behavior

1. Researcher opens the app and uploads a whole-slide image (TIFF/OME-TIFF).
2. App converts the image to a pyramidal OME-TIFF and renders it in VivViewer.
3. Researcher optionally uploads an h5ad file; app runs spatial clustering and overlays colored spots.
4. Researcher clicks a cluster or draws an ROI polygon on the tissue.
5. App runs DEG automatically and caches the gene list (`cluster_context.json` or `roi_context.json`).
6. When a chat message is sent, `routes.py` calls `run_agent(gene_objects, message, label)`.
7. Agent decides whether to call pathway_tool and/or pubmed_tool based on the question; results are formatted into a context string injected into the LLM prompt.
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
    "top_genes": [
        {"gene": "SNAP25", "log2_fold_change": 3.81, "mean_expression": 2.4, ...},
        ...
    ]
}
```

Required behavior:
- Read h5ad path from `spatial_omics.json` in `work_dir`.
- Return `None` if h5ad is not loaded or selection has no spots.
- Rank by log2 fold-change (selected vs reference spots).

### 3.2 Pathway Enrichment

```python
# rag/pathway/__init__.py
def enrich_pathways(genes: list[str], top_n: int = 6) -> list[dict]
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
- Run ORA against GO and KEGG using gseapy or g:Profiler.
- Sort by ascending p-value.
- Return empty list `[]` when no pathways are enriched.

### 3.3 PubMed Retrieval

```python
# rag/pubmed_retrieval/__init__.py
def retrieve_abstracts(genes: list[str], pathways: list[str] = None, n: int = 3) -> list[dict]
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
- Build query from gene symbols and pathway names.
- Call NCBI E-utilities (esearch + efetch).
- Return up to `n` relevant results. If fewer relevant results are found, return fewer results instead of padding with unrelated papers.
- Respect rate limits (3 req/s without key, 10 req/s with `PUBMED_API_KEY`).

### 3.4 Agent Entry Point

```python
# rag/agent/__init__.py
def run_agent(gene_objects, message="", label="selection") -> dict
```

**Input parameters:**

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

The LangGraph agent in `src/rag/agent/graph.py`:
1. Receives the pre-computed gene list (from DEG) along with the user message.
2. Decides whether to call **pathway_tool** (GO / KEGG enrichment) based on the question.
3. Decides whether to call **pubmed_tool** (NCBI abstract retrieval) based on the question.
4. Passes all results to `prompt.py` to build `context_str`.
5. Returns the structured result dict above.

Required behavior:
- `trace` must reflect what the agent actually ran — not a hardcoded list. DEG appears when valid DEG context exists; otherwise the trace should clearly show that DEG context is unavailable.
- Agent decides pathway and/or PubMed based on the user message — both, one, or neither.
- Must not invent gene functions, pathway names, or citations.
- If no tools return results, answer only from the available ROI/gene context and clearly state which evidence sources were unavailable.
- Limit to 5 tool calls per turn to prevent infinite loops.

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
