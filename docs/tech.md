# Technical Design: Spatial Omics Copilot

## 1. Repository Structure

The standard local run command is `python app/app.py --port 8081 --token <token>`.

```text
spatial-omics-copilot/
├── README.md
├── requirements.txt
├── setup.py
├── app/
│   ├── app.py
│   ├── layout.py
│   ├── routes.py
│   ├── session.py
│   ├── inference.py
│   ├── image_utils.py
│   ├── worker.py
│   ├── status_store.py
│   └── utils.py
├── niceview/
│   ├── interface/
│   │   ├── upload.py
│   │   ├── roi.py
│   │   ├── actions.py
│   │   ├── data_io.py
│   │   ├── visualization.py
│   │   ├── callback.py
│   │   └── interface.py
│   └── utils/
│       ├── dataset.py
│       ├── aristotle.py
│       ├── cell.py
│       ├── colors.py
│       ├── rendering.py
│       ├── io.py
│       └── tools.py
├── rag/
│   ├── agent.py
│   ├── pubmed.py
│   ├── pathways.py
│   ├── vectorstore.py
│   └── prompts.py
├── dash_viv_viewer/
│   └── dash_viv_viewer/
│       ├── VivViewer.py
│       ├── utils.py
│       └── __init__.py
└── docs/
```

## 2. Architecture

```text
Browser (Dash + VivViewer)
  -> ROI drawn -> Flask /chat endpoint
  -> worker.py enqueues job
  -> rag/agent.py (LangGraph)
      -> rag/pathways.py    (KEGG/Reactome API)
      -> rag/pubmed.py      (PubMed API)
      -> rag/vectorstore.py (embedding search)
      -> rag/prompts.py     (synthesis prompt)
  -> inference.py streams tokens
  -> session.py writes to chat_sessions/
  -> Browser polls and renders streamed response
```

## 3. Main Modules

| **Module** | **Responsibility** |
| --- | --- |
| `app/app.py` | Entry point, Dash layout mount, top-level callbacks, arg parsing |
| `app/layout.py` | Dash component tree and UI structure |
| `app/routes.py` | All Flask route handlers (chat, upload, preview, OME-TIFF proxy) |
| `app/session.py` | Thread-safe JSON session read/write using fcntl file locking |
| `app/inference.py` | Ollama and OpenAI streaming inference |
| `app/image_utils.py` | ROI image crop, OME-TIFF cache management |
| `app/worker.py` | Background ThreadPoolExecutor job queue for chat processing |
| `app/status_store.py` | File-based upload progress tracking |
| `app/utils.py` | Working directory creation |
| `niceview/interface/roi.py` | Top gene extraction from ROI polygon |
| `niceview/interface/upload.py` | Image and h5ad upload and preprocessing jobs |
| `niceview/interface/visualization.py` | WSI, spot, and cluster overlay generation |
| `niceview/utils/dataset.py` | ThorQuery: main spatial data access class |
| `rag/agent.py` | LangGraph state machine: gene context → tools → synthesis |
| `rag/pubmed.py` | PubMed E-utilities API wrapper |
| `rag/pathways.py` | KEGG/Reactome API wrapper |
| `rag/vectorstore.py` | Embedding store (FAISS or Chroma) for abstract semantic search |
| `rag/prompts.py` | Prompt templates for the synthesis LLM |

## 4. Public APIs

### ROI Gene Context

```python
# niceview/interface/roi.py
def get_roi_high_expression_genes(query, sample_id, roi_coords) -> list[str]:
    """Return top expressed gene names within the ROI polygon."""

def build_roi_gene_context(query, sample_id, roi_coords) -> str:
    """Return a formatted string describing the ROI gene expression for the LLM."""
```

### RAG Agent

`rag/agent.py` does NOT implement a new LLM — it calls `run_model_inference` from
`app/inference.py` as the final step. The agent gathers tool context first, builds
an enriched prompt, then delegates to the existing LLM infrastructure.

```python
# rag/agent.py
def run_agent(gene_context: str, question: str, history: list[dict]) -> Generator[str, None, None]:
    """Stream the agent's response token by token."""
```

### PubMed Tool

```python
# rag/pubmed.py
def search_pubmed(query: str, max_results: int = 5) -> list[dict]:
    """Return list of {title, abstract, pmid, year} dicts."""
```

### Pathway Tool

```python
# rag/pathways.py
def get_pathways(genes: list[str]) -> dict:
    """Return {pathways: [{name, id, p_value}], source: str}."""
```

## 5. Data Models

### Chat Session

```json
{
  "session_id": "hello",
  "messages": [
    {"role": "user", "content": "...", "timestamp": 1234567890},
    {"role": "assistant", "content": "...", "images": ["path/to/roi.png"]}
  ],
  "updated_at": 1234567890
}
```

### ROI Gene Context (passed to agent)

```text
Region of Interest: 245 spots
Top expressed genes: BRCA1 (4.2), TP53 (3.8), MKI67 (3.1), ...
```

## 6. Technology Stack

| **Category** | **Packages / Tools** |
| --- | --- |
| Core | Python 3.11, Flask, Dash, pandas, numpy |
| Image | pyvips, tifffile, rasterio, Pillow, opencv-python |
| Spatial data | anndata, scanpy, scipy |
| Visualization | dash-viv-viewer (local package), plotly |
| LLM | Ollama, OpenAI SDK, Anthropic SDK |
| RAG / Agents | LangChain, LangGraph |
| Embeddings | sentence-transformers, FAISS or Chroma |
| Literature | PubMed E-utilities API (requests) |
| Pathways | KEGG REST API, Reactome API (requests) |
| Session | fcntl, json |
| Testing | pytest |

## 7. Environment Variables

| **Variable** | **Required** | **Purpose** |
| --- | --- | --- |
| `OLLAMA_MODEL` | Yes (if using Ollama) | Model name for local inference |
| `OPENAI_API_KEY` | No | Enables OpenAI GPT models |
| `ANTHROPIC_API_KEY` | No | Enables Claude models |
| `PUBMED_API_KEY` | No | Higher PubMed rate limit |
| `COPILOT_CHAT_DIR` | No | Override chat session storage path |
| `COPILOT_WORKDIR_BASE` | No | Override working directory base path |

Store secrets in `.env` locally. Never commit credentials.

## 8. Technical Risks

| **Risk** | **Mitigation** |
| --- | --- |
| Gigapixel image OOM | Use pyvips streaming; never load full image into RAM |
| LLM hallucinating citations | Constrain prompts; only cite PMIDs returned by PubMed tool |
| PubMed API rate limit | Cache fetched abstracts in vector store; use API key |
| KEGG/Reactome unavailable | Try Reactome as fallback; gracefully inform user if both fail |
| LangGraph agent infinite loop | Set max iterations; hard-stop after N tool calls |
| Session file corruption | Catch JSON parse errors; start fresh session |
| OME-TIFF conversion slow | Run conversion asynchronously with progress bar |
