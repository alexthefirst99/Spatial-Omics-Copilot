# Technical Design: Spatial Omics Copilot

## 1. Repository Structure

```text
spatial-omics-copilot/
├── README.md
├── pyproject.toml
├── requirements.txt
├── setup.py                      # compatibility shim for older tooling
├── app/                          # Dash/Flask application layer
│   ├── app.py                    # Dash entry point + callbacks
│   ├── layout.py                 # Dash UI layout
│   ├── routes.py                 # Flask HTTP routes
│   ├── worker.py                 # background job queue + LLM streaming
│   ├── inference.py              # Ollama / OpenAI API wrapper
│   ├── session.py                # thread-safe chat session read/write
│   ├── image_utils.py            # ROI crop, OME-TIFF cache
│   ├── status_store.py           # upload progress tracking
│   ├── utils.py                  # shared utilities
│   └── assets/
│       ├── chat.js               # chat transport + AGENT TRACE/pathway/DEG panels
│       ├── chat.css              # chat panel styling
│       ├── opioid.css            # main application layout styling
│       ├── s3_upload.js          # browser-side upload handling and progress
│       ├── spinner.css           # loading state styling
│       ├── upload_styles.css     # upload drop zone/progress styling
│       ├── tutorial.js           # tutorial button/client behavior
│       ├── tutorial.css          # tutorial controls styling
│       └── font.css              # font imports
├── src/niceview/                     # UI layer
│   ├── interface/
│   │   ├── upload.py             # image + h5ad upload handlers
│   │   ├── visualization.py      # WSI client, spatial spot overlay, VivViewer setup
│   │   ├── actions.py            # re-visualize, save ROI, clear session/cache
│   │   ├── callback.py           # Dash callbacks
│   │   ├── interface.py          # compatibility exports + workspace mapping
│   │   └── data_io.py            # session data helpers
│   ├── pyplot/
│   │   └── leaflet.py            # Dash VivViewer component builder + cluster legend
│   └── utils/                    # io, dataset, path/viewer helper dependencies
├── src/rag/                          # analysis layer
│   ├── pipeline.py               # fallback sequential pipeline (_run_sequential)
│   ├── preprocessing.py          # QC, normalize, PCA
│   ├── clustering.py             # Leiden / KMeans spatial clustering
│   ├── deg/
│   │   ├── __init__.py           # exposes: get_roi/cluster_high_expression_genes
│   │   └── extraction.py         # DEG computation
│   ├── pathway/
│   │   ├── __init__.py           # exposes: enrich_pathways
│   │   └── enrichment.py         # mock enrichment; target: GO / KEGG ORA
│   ├── pubmed/
│   │   ├── __init__.py           # exposes: retrieve_abstracts
│   │   └── retrieval.py          # mock PubMed retrieval; target: NCBI E-utilities
│   └── agent/
│       ├── __init__.py           # exposes: run_agent  ← only public entry point
│       ├── graph.py              # run_agent wrapper; target: LangGraph agent
│       ├── tools.py              # planned LangChain tools placeholder
│       └── prompt.py             # context string formatting
├── packages/dash_viv_viewer/              # VivViewer React component
└── docs/
```

Run command: `python app/app.py --port 8081 --workspace demo`

Open `http://localhost:8081/workspaces/demo`.

Installed command: `spatial-copilot --port 8081 --workspace demo`

## 2. Architecture

```text
Browser (Dash + VivViewer)
  → cluster clicked / ROI drawn
      → app.py callback calls rag.deg immediately
      → caches gene list to cluster_context.json / roi_context.json
  → user sends chat message → Flask /chat endpoint (routes.py)
      → routes.py loads cached gene_objects
      → run_agent(gene_objects, message, label)   ← only call into rag/
      → routes.py enqueues job with {rag_context_str, rag_metadata}
      → worker.py injects context_str into messages → inference.py streams tokens
      → session.py writes to data/chat_sessions/
      → browser polls and renders streamed response

Target run_agent() internals (src/rag/agent/graph.py):
  → pathway tool (src/rag/pathway/)   — GO / KEGG ORA
  → pubmed tool  (src/rag/pubmed/)    — NCBI abstract retrieval
  → prompt.py   formats context string
  → returns {gene_objects, context_str, metadata}

Current fallback:
  → src/rag/agent/graph.py calls src/rag/pipeline._run_sequential()
  → _run_sequential() always runs mock pathway + mock PubMed retrieval
```

## 3. Module Responsibilities

### app/

| **Module** | **Responsibility** |
| --- | --- |
| `app/app.py` | Dash entry point — registers layout, callbacks, and Flask server |
| `app/layout.py` | Dash UI layout definition (components, IDs) |
| `app/routes.py` | Flask HTTP handlers — `/chat`, `/chat/poll`, `/chat/clear`, `/ome_tiff`, `/preview`; calls `run_agent()`, enqueues jobs |
| `app/worker.py` | Background job queue — injects RAG context into messages, calls `inference.py`, writes stream |
| `app/inference.py` | Ollama and OpenAI streaming LLM wrapper |
| `app/session.py` | Thread-safe chat session read/write (fcntl locking, atomic writes) |
| `app/image_utils.py` | ROI image crop and OME-TIFF pyramidal cache management |
| `app/status_store.py` | File-based upload progress tracking (progress bar state) |
| `app/utils.py` | Shared utilities — working directory setup, path helpers |
| `app/assets/chat.js` | Chat client, stream polling, active-layer reporting, RAG metadata rendering |
| `app/assets/s3_upload.js` | Browser-side upload handling and progress updates |
| `app/assets/*.css` | Layout, chat, upload, tutorial, and spinner styling |

### src/niceview/interface/

| **Module** | **Responsibility** |
| --- | --- |
| `src/niceview/interface/callback.py` | Dash callback aggregator — re-exports all callbacks from `upload.py` and `actions.py` |
| `src/niceview/interface/upload.py` | Image and h5ad upload handlers; validates spatial coordinates; triggers clustering |
| `src/niceview/interface/visualization.py` | WSI client setup, spatial spot extraction, optional cluster overlay image, VivViewer assembly |
| `src/niceview/interface/actions.py` | Re-visualize callback helper, ROI JSON/coordinate persistence, session/cache cleanup |
| `src/niceview/interface/interface.py` | Compatibility exports from `data_io.py`/`visualization.py` plus workspace mapping |
| `src/niceview/interface/data_io.py` | Session data read/write helpers using `ThorQuery` |

### src/niceview/utils/ and src/niceview/pyplot/

`app.py` directly imports only `niceview.utils.io`. The other files below are reached indirectly through `niceview.interface.*` helpers.

| **Module** | **Current app call path** | **Responsibility** |
| --- | --- | --- |
| `src/niceview/utils/io.py` | Direct: `app.py` and `routes.py` import `niceview.utils.io as vio` | File I/O wrapper for JSON, TOML, paths, images, arrays, and cache files |
| `src/niceview/utils/dataset.py` | Indirect: `app.py` → `niceview.interface.interface/data_io.py` → `ThorQuery` | Data/cache client currently used for WSI generation and viewer tile clients |
| `src/niceview/utils/aristotle.py` | Indirect: `ThorQuery` → `AristotleDataset` | Constructs data/cache filenames from sample IDs and field names |
| `src/niceview/utils/colors.py` | Indirect: `leaflet.py` imports viewer constants and color helpers from it | Provides colormap constants and helper functions used by viewer legend code |
| `src/niceview/pyplot/leaflet.py` | Indirect but active: `app.py` → `reset()`/viewer callbacks → `visualization.py` → `create_viv_viewer()` | Builds `VivViewer`, image URLs, ROI props, and clickable cluster legend |

### src/rag/

| **Module** | **Responsibility** |
| --- | --- |
| `src/rag/pipeline.py` | Current fallback sequential pipeline — always runs pathway + PubMed context assembly |
| `src/rag/preprocessing.py` | QC, normalization, HVG selection, PCA on h5ad |
| `src/rag/clustering.py` | Leiden / KMeans spatial clustering, saves cluster JSON |
| `src/rag/deg/extraction.py` | DEG extraction from cluster or ROI selection |
| `src/rag/pathway/enrichment.py` | Current mock pathway enrichment over hardcoded gene sets; target: real ORA against GO / KEGG |
| `src/rag/pubmed/retrieval.py` | Current mock PubMed retrieval over curated abstracts; target: NCBI E-utilities retrieval |
| `src/rag/agent/graph.py` | Current `run_agent()` wrapper around `_run_sequential()`; target: LangGraph tool-selection agent |
| `src/rag/agent/tools.py` | Placeholder for planned LangChain tools: `pathway_tool`, `pubmed_tool` |
| `src/rag/agent/prompt.py` | Formats RAG evidence into LLM context string |

## 4. Single Entry Point Contract

`routes.py` is the only caller of `run_agent`. `app.py` calls DEG directly (not `run_agent`):

```python
# routes.py — called when user sends a chat message
from rag.agent import run_agent
result = run_agent(gene_objects, message="", label="selection")

# app.py — called immediately when user clicks cluster or draws ROI
from rag.deg import get_cluster_high_expression_genes, get_roi_high_expression_genes
```

See `docs/specs.md` section 3.4 for the full `run_agent` input/output contract.

```python
{
    "gene_objects": [...],   # gene list passed back through (from input)
    "context_str":  "...",   # evidence string → prepended to LLM prompt (worker.py)
    "metadata": {
        "trace":     [...],  # steps agent ran → AGENT TRACE card in chat UI
        "degs":      [...],  # top 8 DEGs → bar chart in chat UI
        "pathways":  [...],  # enriched pathways → bar chart in chat UI
        "citations": [...],  # PubMed abstracts → citation list in chat UI
        "label":     "...",  # region label → panel headers
    }
}
```

## 5. LLM Flow

The `src/rag/` layer does **not** call the LLM. It returns `context_str` as data.
The LLM call happens in `app/`:

```text
routes.py  → run_agent() → returns {context_str, metadata}
worker.py  → appends context_str to messages → inference.py → streams tokens
```

## 6. Technology Stack

| **Category** | **Current** | **Planned / Target** |
| --- | --- | --- |
| Core | Python 3.11, Flask, Dash | — |
| Image | pyvips/OME-TIFF conversion path, tifffile, Pillow, opencv-python | — |
| Spatial data | anndata, scanpy, scipy, scikit-learn | — |
| Visualization | local `dash_viv_viewer`, Plotly/Dash components | — |
| LLM | Ollama Python client, OpenAI Chat Completions HTTP call | — |
| RAG / Agents | `_run_sequential()` fallback | LangGraph + LangChain tools |
| Pathway | Hardcoded mock gene-set enrichment | gseapy / GO / KEGG ORA |
| Literature | Hardcoded mock curated abstracts | PubMed E-utilities API |
| Vector store | Not active in current code | chromadb or faiss-cpu |
| Session | fcntl, json | — |

## 7. Environment Variables

| **Variable** | **Required** | **Purpose** |
| --- | --- | --- |
| `OLLAMA_MODEL` | No | Override local Ollama model; defaults to `qwen3-vl:30b` |
| `OLLAMA_HOST` | No | Override Ollama server URL; defaults to `http://localhost:11435` |
| `OPENAI_API_KEY` | Yes if using OpenAI provider | Enables OpenAI chat responses |
| `OPENAI_MODEL` | No | Override OpenAI model; defaults to `gpt-4o` |
| `OPENAI_INSECURE_SSL` | No | Local-dev escape hatch; `1` disables OpenAI SSL verification retry |
| `COPILOT_CHAT_DIR` | No | Override chat session path |
| `COPILOT_WORKDIR_BASE` | No | Override working directory path |
| `COPILOT_TMP_BASE` | No | Override temporary upload/OME-TIFF cache path |
| `COPILOT_STATUS_DIR` | No | Override upload status JSON path |
| `COPILOT_WORKSPACE_MAP` | No | Override workspace-to-port map path |
| `COPILOT_TUTORIAL_IMAGE` | No | Override local tutorial OME-TIFF path |
| `PUBMED_API_KEY` | Planned, not active | Intended for future live PubMed E-utilities rate limits |

## 8. Technical Risks

| **Risk** | **Mitigation** |
| --- | --- |
| Gigapixel image OOM | pyvips streaming — never load full image into RAM |
| LLM hallucinating citations | Only cite PMIDs returned by PubMed tool |
| Future PubMed rate limit | Cache live retrieval results; use `PUBMED_API_KEY` when NCBI integration lands |
| Future LangGraph infinite loop | Enforce max 5 tool calls per turn when replacing `_run_sequential()` |
| Session file corruption | Catch JSON errors; start fresh session |
| OME-TIFF conversion slow | Async conversion with progress bar |
