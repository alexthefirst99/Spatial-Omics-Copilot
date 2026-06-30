# Technical Design: Spatial Omics Copilot

## 1. Repository Structure

```text
spatial-omics-copilot/
├── README.md
├── requirements.txt
├── setup.py
├── app/                          # infrastructure layer
│   ├── app.py                    # Dash entry point + callbacks
│   ├── layout.py                 # Dash UI layout
│   ├── routes.py                 # Flask HTTP routes
│   ├── worker.py                 # background job queue + LLM streaming
│   ├── inference.py              # Ollama / OpenAI API wrapper
│   ├── session.py                # thread-safe chat session read/write
│   ├── image_utils.py            # ROI crop, OME-TIFF cache
│   ├── status_store.py           # upload progress tracking
│   └── assets/
│       ├── chat.js               # AGENT TRACE, pathway/DEG panels
│       └── opioid.css
├── niceview/                     # UI layer
│   ├── interface/
│   │   ├── upload.py             # image + h5ad upload handlers
│   │   ├── visualization.py      # spot overlay, VivViewer setup
│   │   ├── actions.py            # re-visualize, save ROI
│   │   ├── callback.py           # Dash callbacks
│   │   └── data_io.py            # session data helpers
│   └── utils/                    # io, colors, rendering helpers
├── rag/                          # analysis layer
│   ├── pipeline.py               # fallback sequential pipeline (_run_sequential)
│   ├── preprocessing.py          # QC, normalize, PCA
│   ├── clustering.py             # Leiden / KMeans spatial clustering
│   ├── deg/
│   │   ├── __init__.py           # exposes: get_roi/cluster_high_expression_genes
│   │   └── extraction.py         # DEG computation
│   ├── pathway/
│   │   ├── __init__.py           # exposes: enrich_pathways
│   │   └── enrichment.py         # ORA against GO / KEGG
│   ├── pubmed/
│   │   ├── __init__.py           # exposes: retrieve_abstracts
│   │   └── retrieval.py          # NCBI E-utilities + vector store
│   └── agent/
│       ├── __init__.py           # exposes: run_agent  ← only public entry point
│       ├── graph.py              # LangGraph agent (currently mock)
│       ├── tools.py              # LangChain tool definitions
│       └── prompt.py             # context string formatting
├── dash_viv_viewer/              # VivViewer React component
└── docs/
```

Run command: `python app/app.py --port 8081 --token hello`

## 2. Architecture

```text
Browser (Dash + VivViewer)
  → ROI drawn / cluster clicked → Flask /chat endpoint (routes.py)
  → run_agent(message, work_dir, cluster_id, coords)   ← only call into rag/
  → routes.py enqueues job with {rag_context_str, rag_metadata}
  → worker.py injects context_str into messages → inference.py streams tokens
  → session.py writes to chat_sessions/
  → browser polls and renders streamed response

run_agent() internals (rag/agent/graph.py):
  → deg tool    (rag/deg/)
  → pathway tool (rag/pathway/)
  → pubmed tool  (rag/pubmed/)
  → prompt.py   formats context string
  → returns {gene_objects, context_str, metadata}
```

## 3. Module Responsibilities

| **Module** | **Responsibility** |
| --- | --- |
| `app/routes.py` | HTTP handlers — calls `run_agent()`, enqueues job |
| `app/worker.py` | Background job — injects RAG context, calls `inference.py` |
| `app/inference.py` | Ollama and OpenAI streaming LLM calls |
| `app/session.py` | Thread-safe JSON session read/write (fcntl locking) |
| `app/image_utils.py` | ROI image crop, OME-TIFF cache management |
| `niceview/interface/upload.py` | Image and h5ad upload, triggers clustering |
| `niceview/interface/visualization.py` | Spot overlay and VivViewer setup |
| `rag/pipeline.py` | Fallback sequential pipeline (used until LangGraph is ready) |
| `rag/preprocessing.py` | QC, normalization, HVG, PCA on h5ad |
| `rag/clustering.py` | Leiden / KMeans spatial clustering, saves cluster JSON |
| `rag/deg/extraction.py` | DEG extraction from cluster or ROI |
| `rag/pathway/enrichment.py` | ORA against GO / KEGG |
| `rag/pubmed/retrieval.py` | PubMed NCBI API + vector store |
| `rag/agent/graph.py` | LangGraph agent — decides which tools to call |
| `rag/agent/tools.py` | LangChain tool definitions wrapping rag submodules |
| `rag/agent/prompt.py` | Formats RAG evidence into LLM context string |

## 4. Single Entry Point Contract

`routes.py` and `app.py` only ever call one function from `rag/`:

```python
from rag.agent import run_agent

result = run_agent(work_dir, message="", cluster_id=None, coords=None, folder_id="")
```

Returns:
```python
{
    "gene_objects": [{"gene": "SNAP25", "log2_fold_change": 3.81}, ...],
    "context_str":  "\n\nRAG-retrieved biological context...",
    "metadata": {
        "trace":     [{"step": "...", "detail": "...", "icon": "..."}, ...],
        "degs":      [{"gene": "SNAP25", "log2fc": 3.81}, ...],
        "pathways":  [{"source": "GO", "name": "...", "neg_log10p": 5.1, "gene_count": 8}, ...],
        "citations": [{"id": 1, "pmid": "...", "title": "...", "journal": "...", "year": 2024}, ...],
        "label":     "Cluster 2",
    }
}
```

## 5. LLM Flow

The `rag/` layer does **not** call the LLM. It returns `context_str` as data.
The LLM call happens in `app/`:

```text
routes.py  → run_agent() → returns {context_str, metadata}
worker.py  → appends context_str to messages → inference.py → streams tokens
```

## 6. Technology Stack

| **Category** | **Packages** |
| --- | --- |
| Core | Python 3.11, Flask, Dash |
| Image | pyvips, tifffile, Pillow, opencv-python |
| Spatial data | anndata, scanpy, scipy, scikit-learn |
| Visualization | dash-viv-viewer (local), plotly |
| LLM | Ollama, OpenAI SDK |
| RAG / Agents | LangChain, LangGraph |
| Pathway | gseapy (GO / KEGG ORA) |
| Literature | PubMed E-utilities API (requests), biopython |
| Vector store | chromadb or faiss-cpu |
| Session | fcntl, json |

## 7. Environment Variables

| **Variable** | **Required** | **Purpose** |
| --- | --- | --- |
| `OLLAMA_MODEL` | Yes (if Ollama) | Local model name |
| `OPENAI_API_KEY` | No | Enables OpenAI models (e.g. gpt-4o) |
| `PUBMED_API_KEY` | No | Higher PubMed rate limit (10 req/s) |
| `COPILOT_CHAT_DIR` | No | Override chat session path |
| `COPILOT_WORKDIR_BASE` | No | Override working directory path |

## 8. Technical Risks

| **Risk** | **Mitigation** |
| --- | --- |
| Gigapixel image OOM | pyvips streaming — never load full image into RAM |
| LLM hallucinating citations | Only cite PMIDs returned by PubMed tool |
| PubMed rate limit | Cache in vector store; use API key |
| LangGraph infinite loop | Max 5 tool calls per turn |
| Session file corruption | Catch JSON errors; start fresh session |
| OME-TIFF conversion slow | Async conversion with progress bar |
