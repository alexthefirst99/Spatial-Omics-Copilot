# Functional Specifications: Spatial Omics Copilot

## 1. End-to-End Behavior

1. Researcher opens the app and uploads a whole-slide image (TIFF/OME-TIFF).
2. App converts the image to a pyramidal OME-TIFF and renders it in VivViewer.
3. Researcher optionally uploads an h5ad file; app runs spatial clustering and overlays colored spots.
4. Researcher clicks a cluster or draws an ROI polygon on the tissue.
5. App calls `run_agent()` — the single entry point into the RAG pipeline.
6. Agent decides which tools to call (DEG, pathway, PubMed) based on the question.
7. Tool results are formatted into a context string and injected into the LLM prompt.
8. LLM response streams token by token to the chat interface.
9. Chat UI shows AGENT TRACE card, pathway bar chart, DEG bar chart, then streamed text.
10. Researcher can ask follow-up questions; agent continues with full region context.

## 2. Data Input Contract

| **Input** | **Format** | **Required Behavior** |
| --- | --- | --- |
| Whole-slide image | .tiff, .ome.tiff, .svs | Convert to pyramidal OME-TIFF; render via VivViewer |
| Gene expression | .h5ad | Validate `adata.obsm["spatial"]`; run clustering; overlay spots |
| Cluster selection | cluster label string | Passed as `cluster_id` to `run_agent()` |
| ROI polygon | list of coordinate lists | Passed as `coords` to `run_agent()` |

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
# rag/pubmed/__init__.py
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
- Always return exactly `n` results; pad with less relevant results if needed.
- Respect rate limits (3 req/s without key, 10 req/s with `PUBMED_API_KEY`).

### 3.4 Agent Entry Point

```python
# rag/agent/__init__.py
def run_agent(work_dir, message="", cluster_id=None, coords=None, folder_id="") -> dict
```

Returns:
```python
{
    "gene_objects": [{"gene": str, "log2_fold_change": float}, ...],
    "context_str":  str,   # injected into LLM prompt by worker.py
    "metadata": {
        "trace":     [{"step": str, "detail": str, "icon": str}, ...],
        "degs":      [{"gene": str, "log2fc": float}, ...],
        "pathways":  [{"source": str, "name": str, "neg_log10p": float, "gene_count": int}, ...],
        "citations": [{"id": int, "pmid": str, "title": str, "journal": str, "year": int}, ...],
        "label":     str,
    }
}
```

## 4. Agent Behavior

The LangGraph agent in `rag/agent/graph.py`:
1. Receives the user message, `work_dir`, `cluster_id` or `coords`.
2. Decides which tools to call based on the question.
3. Calls DEG tool, pathway tool, PubMed tool as needed.
4. Passes results to `prompt.py` to build `context_str`.
5. Returns the structured result dict above.

Required behavior:
- `trace` must reflect what the agent **actually** called — not a hardcoded list.
- Must call at least one tool before answering a question about a tissue region.
- Must not invent gene functions, pathway names, or citations.
- If all tools return empty, return demo fallback genes and label as "demo".
- Limit to 5 tool calls per turn to prevent infinite loops.

## 5. Chat Interface Behavior

- One session per token; stored in `chat_sessions/<session_id>/session.json`.
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
| ROI contains no spots | Return empty gene list; agent uses demo fallback |
| Pathway API unavailable | Return empty list; agent answers from gene context only |
| PubMed returns no results | Return empty list; inform user |
| LLM unavailable | Show error message; do not stream partial answer |
| h5ad missing spatial coordinates | Reject with clear error on upload |
| No h5ad loaded | Agent uses demo gene list (12 brain genes) |
| Image too large for memory | Use pyvips streaming |
| Session file corrupted | Start fresh session; log error |
