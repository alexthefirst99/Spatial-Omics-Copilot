# Technical Design: Spatial Omics Copilot

## 1. Repository Structure

```text
spatial-omics-copilot/
├── README.md
├── pyproject.toml
├── requirements.txt
├── config/
│   └── app.yaml                  # general app settings
├── app/                          # Dash/Flask application layer
│   ├── app.py                    # Dash entry point + callbacks
│   ├── layout.py                 # Dash UI layout
│   ├── routes.py                 # Flask HTTP routes
│   ├── worker.py                 # background job queue + LLM streaming
│   ├── inference.py              # Ollama API wrapper
│   ├── config.py                 # config/app.yaml + env var resolution
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
│   ├── contracts.py              # shared result types every module returns instead of
│   │                              # ad hoc dicts: PreprocessResult, ClusterResult,
│   │                              # ROISelection, ROIImageResult, DEGResult/GeneStat,
│   │                              # AgentResult/TraceStep/Citation, and friends
│   ├── pipeline.py               # run_integration_pipeline(): preprocess -> cluster ->
│   │                              # resolve ROI -> crop image -> DEG -> gene annotation ->
│   │                              # pathway -> PubMed -> copilot_agent, using every
│   │                              # module's real function; also still hosts
│   │                              # _run_sequential(), an offline fallback kept for
│   │                              # local/no-network use
│   ├── preprocessing.py           # QC, normalize, HVG, PCA; cached to disk (T-035)
│   ├── clustering.py              # Leiden / KMeans spatial clustering; cached to disk (T-040)
│   ├── deg/
│   │   ├── __init__.py           # exposes: get_roi/cluster_high_expression_genes, run_roi_deg
│   │   ├── extraction.py         # DEG computation, ROI/cluster selection resolution
│   │   ├── filtering.py          # pre-filter low-count genes before testing (T-010)
│   │   ├── geometry.py           # ROI polygon -> spot mask resolution
│   │   ├── models.py             # legacy home of DEGResult/GeneStat; now re-exports
│   │   │                          # the canonical definitions in rag.contracts
│   │   ├── stats.py              # vectorised Wilcoxon rank-sum + Benjamini-Hochberg
│   │   └── workspace.py          # work_dir/session path resolution for DEG
│   ├── pathway_enrichment/       # real GO / KEGG ORA
│   │   ├── __init__.py           # exposes: run_pathway_enrichment
│   │   ├── enrichment.py         # gseapy/Enrichr calls, one HTTP call per gene-set
│   │   │                          # library (Enrichr silently drops all but one
│   │   │                          # library's results when queried together)
│   │   └── models.py             # PathwayEntry / PathwayResult
│   ├── gene_annotation/          # NCBI Gene functional annotation
│   │   ├── __init__.py           # exposes: run_gene_annotation_retrieval
│   │   ├── client.py             # NCBI Gene ESearch/ESummary client
│   │   └── models.py             # GeneAnnotation / GeneAnnotationResult
│   ├── pubmed_retrieval/         # live NCBI literature retrieval
│   │   ├── __init__.py           # exposes: search_pubmed, build_pubmed_query,
│   │   │                          # semantic_search_abstracts
│   │   ├── client.py             # rate-limited NCBI ESearch/EFetch + XML parsing
│   │   ├── query.py              # gene/pathway/disease query builder
│   │   ├── retrieval.py          # PubMedResult + legacy schema adapter
│   │   └── vector_store.py       # ChromaDB semantic search over retrieved abstracts
│   ├── copilot_agent/            # the real LangGraph agent
│   │   ├── __init__.py           # exposes: run_agent (frozen legacy contract),
│   │   │                          # run_copilot_agent (extensible entry point)
│   │   ├── graph.py               # LangGraph state machine: route -> run_tool (loop) ->
│   │   │                          # synthesize; falls back to running the same node
│   │   │                          # functions sequentially if langgraph is unavailable
│   │   ├── routing.py             # question -> intent -> tool work-queue classification
│   │   ├── tools.py               # gene_annotation_tool, pathway_tool, pubmed_tool
│   │   ├── prompt.py              # evidence-block formatting, citation discipline,
│   │   │                          # prompt-injection fencing, disease-context statement
│   │   ├── multimodal.py          # cropped ROI image -> vision-model payload
│   │   ├── llm.py                 # DeepInfra chat client (optional; app/ still owns
│   │   │                          # the actual Ollama call for the real chat reply)
│   │   ├── adapters.py            # tolerant field access across dict/dataclass results
│   │   └── models.py              # legacy home of AgentResult/TraceStep/Citation; now
│   │                               # re-exports the canonical definitions in rag.contracts
│   ├── agent/                    # back-compat: `from rag.agent import run_agent` still
│   │   └── __init__.py           # works; re-exports from copilot_agent, no logic of its own
│   ├── pathway/                  # back-compat import path; real logic is in pathway_enrichment/
│   └── pubmed/                   # back-compat import path; real logic is in pubmed_retrieval/
├── packages/dash_viv_viewer/              # VivViewer React component
├── data/demo/                    # 10x Visium HD Human Colon Cancer demo dataset
├── docs/
│   └── validation/                # per-person biological/functional validation notes
└── (HPC launcher scripts, see docs/validation notes — not part of the installable package)
```

Run command: `python app/app.py -port 8081 -workspace demo`

Open `http://localhost:8081/workspaces/demo`.

Installed command: `spatial-copilot -port 8081 -workspace demo`

## 2. Architecture

```text
Browser (Dash + VivViewer)
  → cluster clicked / ROI drawn
      → app.py callback calls rag.deg immediately
      → crops the ROI out of the whole-slide image once (cached to disk)
      → caches the gene list to cluster_context.json / roi_context.json
      → erasing the selection clears all three caches, not just the raw
        coordinates — otherwise the chat kept silently answering about the
        previous selection
  → user sends chat message → Flask /chat endpoint (routes.py)
      → routes.py loads the cached gene_objects and cropped ROI image
      → extracts disease/sample context from the conversation once per
        session (cached after the first success — the model call behind
        this is too slow to repeat on every turn)
      → routes.py calls run_copilot_agent(question, deg, label, disease, ...)
        directly, not the frozen run_agent() wrapper, so the extracted
        disease value and other newer parameters can actually reach it
      → routes.py enqueues the job with {rag_context_str, rag_metadata}
      → worker.py appends context_str to the latest message, attaches the
        cropped ROI image if a vision model is selected, and sends the
        last 4 turns of conversation (bounded — Ollama re-processes the
        whole prompt from scratch on every call, so unbounded history would
        make every response progressively slower as a session gets longer)
      → inference.py streams tokens from Ollama (or DeepInfra, if configured)
      → session.py writes to data/chat_sessions/
      → browser polls and renders streamed response

run_copilot_agent() internals (src/rag/copilot_agent/graph.py), one
LangGraph superstep per tool call:
  → route: classify the question into an intent and a tool work-queue
  → run_tool (looped, up to 5 calls per turn): gene_annotation_tool,
    pathway_tool, and/or pubmed_tool — only the ones the question and the
    routing decision actually call for; a general/irrelevant question calls
    none of them
  → synthesize: prompt.py builds the evidence block (DEG genes, gene
    annotations, pathways, PubMed abstracts, and the disease-context
    statement when one was actually extracted — never asserting a guessed
    default) and returns {gene_objects, context_str, metadata}

Two entry points share this machinery:
  run_agent(gene_objects, message, label) — the legacy contract app/routes.py
    used to depend on; signature frozen, must not change.
  run_copilot_agent(question, deg, label, disease, ...) — the extensible
    entry point; what app/routes.py actually calls now, and what the
    integration pipeline (rag/pipeline.py) calls too.

Offline fallback (no network, or LangGraph unavailable):
  → rag/pipeline.py's _run_sequential() always runs pathway + PubMed
    retrieval directly, without the agent deciding whether they're needed
```

## 3. Module Responsibilities

### app/

| **Module** | **Responsibility** |
| -- | -- |
| `app/app.py` | Dash entry point — registers layout, callbacks, and Flask server |
| `app/layout.py` | Dash UI layout definition (components, IDs); default chat model is the vision model `qwen2.5vl:7b` |
| `app/routes.py` | Flask HTTP handlers — `/chat`, `/chat/poll`, `/chat/clear`, `/ome_tiff`, `/preview`; extracts/caches disease context; calls `run_copilot_agent()` directly; enqueues jobs |
| `app/worker.py` | Background job queue — builds the bounded-history message list, appends RAG context, attaches the ROI crop for vision models, calls `inference.py`, writes the stream |
| `app/inference.py` | Ollama streaming LLM wrapper |
| `app/config.py` | Reads `config/app.yaml`, applies environment variable overrides |
| `app/session.py` | Thread-safe chat session read/write (fcntl locking, atomic writes) |
| `app/image_utils.py` | ROI image crop and OME-TIFF pyramidal cache management |
| `app/status_store.py` | File-based upload progress tracking (progress bar state) |
| `app/utils.py` | Shared utilities — working directory setup, path helpers |
| `app/assets/chat.js` | Chat client, stream polling, active-layer reporting, RAG metadata rendering, clickable PubMed citation links |
| `app/assets/s3_upload.js` | Browser-side upload handling and progress updates |
| `app/assets/*.css` | Layout, chat, upload, tutorial, and spinner styling |

### src/niceview/interface/

| **Module** | **Responsibility** |
| -- | -- |
| `src/niceview/interface/callback.py` | Dash callback aggregator — re-exports all callbacks from `upload.py` and `actions.py` |
| `src/niceview/interface/upload.py` | Image and h5ad upload handlers; validates spatial coordinates; triggers clustering |
| `src/niceview/interface/visualization.py` | WSI client setup, spatial spot extraction, optional cluster overlay image, VivViewer assembly |
| `src/niceview/interface/actions.py` | Re-visualize callback helper, ROI JSON/coordinate persistence, session/cache cleanup |
| `src/niceview/interface/interface.py` | Compatibility exports from `data_io.py`/`visualization.py` plus workspace mapping |
| `src/niceview/interface/data_io.py` | Session data read/write helpers using `ThorQuery` |

### src/niceview/utils/ and src/niceview/pyplot/

`app.py` directly imports only `niceview.utils.io`. The other files below are reached indirectly through `niceview.interface.*` helpers.

| **Module** | **Current app call path** | **Responsibility** |
| -- | -- | -- |
| `src/niceview/utils/io.py` | Direct: `app.py` and `routes.py` import `niceview.utils.io as vio` | File I/O wrapper for JSON, TOML, paths, images, arrays, and cache files |
| `src/niceview/utils/dataset.py` | Indirect: `app.py` → `niceview.interface.interface/data_io.py` → `ThorQuery` | Data/cache client currently used for WSI generation and viewer tile clients |
| `src/niceview/utils/aristotle.py` | Indirect: `ThorQuery` → `AristotleDataset` | Constructs data/cache filenames from sample IDs and field names |
| `src/niceview/utils/colors.py` | Indirect: `leaflet.py` imports viewer constants and color helpers from it | Provides colormap constants and helper functions used by viewer legend code |
| `src/niceview/pyplot/leaflet.py` | Indirect but active: `app.py` → `reset()`/viewer callbacks → `visualization.py` → `create_viv_viewer()` | Builds `VivViewer`, image URLs, ROI props, and clickable cluster legend |

### src/rag/

| **Module** | **Responsibility** |
| -- | -- |
| `src/rag/contracts.py` | Shared result dataclasses used across every module below — `PreprocessResult`, `ClusterResult`, `ROISelection`, `ROIImageResult`, `DEGResult`/`GeneStat`, `AgentResult`/`TraceStep`/`Citation` |
| `src/rag/pipeline.py` | `run_integration_pipeline()` wiring every module together; `_run_sequential()` kept as an offline fallback |
| `src/rag/preprocessing.py` | QC, normalization, HVG selection, PCA on h5ad; cached to disk |
| `src/rag/clustering.py` | Leiden / KMeans spatial clustering, saves cluster JSON; cached to disk |
| `src/rag/deg/extraction.py` | DEG extraction from cluster or ROI selection, Wilcoxon rank-sum + BH correction |
| `src/rag/pathway_enrichment/enrichment.py` | Real ORA against GO / KEGG via `gseapy`/Enrichr |
| `src/rag/gene_annotation/retrieval.py` | Real NCBI Gene functional annotation retrieval |
| `src/rag/pubmed_retrieval/` | Live NCBI ESearch/EFetch, safe result envelope, query building, and ChromaDB semantic search |
| `src/rag/copilot_agent/graph.py` | The real LangGraph agent — dynamic tool routing, evidence assembly, disease-context statement |
| `src/rag/copilot_agent/tools.py` | `gene_annotation_tool`, `pathway_tool`, `pubmed_tool` — the agent's actual tool implementations |
| `src/rag/copilot_agent/prompt.py` | Formats RAG evidence + disease context into the LLM context string |

## 4. Entry Point Contracts

`app.py` calls DEG directly when the user clicks a cluster or draws an ROI (not through the agent):

```python
from rag.deg import get_cluster_high_expression_genes, get_roi_high_expression_genes
```

`routes.py` calls the agent when a chat message is sent — via the extensible entry point, not the frozen legacy one, so newer parameters like `disease` can reach it:

```python
from rag.agent import run_copilot_agent   # re-exported from rag.copilot_agent
result = run_copilot_agent(
    question=user_message,
    deg=gene_objects,
    label=label,
    disease=extracted_disease_or_none,
).to_legacy_dict()
```

The frozen legacy contract, `run_agent(gene_objects, message="", label="selection")`, still exists and still returns the same shape — it is a thin wrapper around `run_copilot_agent()` with no `disease` parameter. See `docs/specs.md` section 3.4 for the full output contract:

```python
{
    "gene_objects": [...],   # gene list passed back through (from input)
    "context_str":  "...",   # evidence string → prepended to LLM prompt (worker.py)
    "metadata": {
        "trace":     [...],  # steps the agent actually ran → AGENT TRACE card in chat UI
        "degs":      [...],  # top 8 DEGs → bar chart in chat UI
        "pathways":  [...],  # enriched pathways → bar chart in chat UI
        "citations": [...],  # PubMed abstracts → citation list in chat UI (clickable PMID links)
        "label":     "...",  # region label → panel headers
    }
}
```

## 5. LLM Flow

The `src/rag/` layer does **not** call the LLM for the actual chat reply — it returns `context_str` as data. The exception is `app/routes.py`'s disease-context extraction, which is itself `app/` code (not `src/rag/`) making a small, separate Ollama call to classify the conversation, cached after the first success:

```text
routes.py  → _extract_disease_context() → one small Ollama call, cached per session
routes.py  → run_copilot_agent() → returns {context_str, metadata}
worker.py  → appends context_str to messages, attaches ROI crop if a vision
              model is selected → inference.py → streams tokens
```

## 6. Technology Stack

| **Category** | **Current** |
| -- | -- |
| Core | Python 3.11, Flask, Dash |
| Image | pyvips/OME-TIFF conversion path, tifffile, Pillow, opencv-python, rasterio |
| Spatial data | anndata, scanpy, scipy, scikit-learn |
| Visualization | local `dash_viv_viewer`, Plotly/Dash components |
| LLM | Ollama Python client (default); DeepInfra HTTP client (optional, `copilot_agent/llm.py`) |
| RAG / Agents | LangGraph state machine with dynamic tool selection (`copilot_agent/graph.py`); sequential fallback (`pipeline._run_sequential()`) for offline/no-LangGraph use |
| Pathway | Real ORA via `gseapy`/Enrichr against GO Biological Process and KEGG |
| Gene annotation | Real NCBI Gene ESearch/ESummary retrieval |
| Literature | PubMed E-utilities ESearch + EFetch (called synchronously in the request path — see Technical Risks) |
| Vector store | ChromaDB, optional semantic re-ranking of retrieved abstracts |
| Certificates | `pip-system-certs` — makes `requests`/`urllib3` trust the OS certificate store, needed on networks with a TLS-inspecting firewall (e.g. a hospital network) whose CA the OS trusts but `certifi`'s bundled list does not |
| Session | fcntl, json |

## 7. Configuration

General settings live in `config/app.yaml`. Secrets live in `.env`.

| **YAML key / env override** | **Required** | **Purpose** |
| -- | -- | -- |
| `ollama.model` / `OLLAMA_MODEL` | No | Local Ollama model; defaults to `qwen2.5vl:7b` |
| `ollama.host` / `OLLAMA_HOST` | No | Ollama server URL; defaults to `http://localhost:11434` |
| `ollama.num_predict` | No | Safety ceiling on generated tokens — the prompt itself instructs the model to answer in ~3-4 sentences, so this only needs to be large enough for that to actually finish rather than cut off mid-sentence |
| `ollama.warmup` / `OLLAMA_WARMUP` | No | If true, sends a throwaway prompt at startup so the first real chat message isn't slowed by a cold model load |
| `ollama.vision_model` / `OLLAMA_VISION_MODEL` | No | Model used for turns with an image attached; defaults to `ollama.model`'s value |
| `ollama.timeout` / `OLLAMA_TIMEOUT` | No | Ollama request timeout in seconds |
| `ollama.keep_alive` / `OLLAMA_KEEP_ALIVE` | No | How long Ollama keeps a model loaded in memory between requests |
| `app.hot_reload` / `COPILOT_HOT_RELOAD` | No | Enable Dash dev-tools hot reload |
| `copilot_agent.max_tool_calls` | No | Hard cap on tool calls per turn; defaults to 5 |
| `copilot_agent.semantic_rerank` | No | Re-rank retrieved abstracts against the question with ChromaDB; off by default |
| `pathway_enrichment.*` | No | `gene_sets`, `organism`, `top_n`, `max_genes`, `adjusted_p_value_cutoff`, `significant_only` — real ORA query/filter parameters for `gseapy.enrichr()` |
| `gene_annotation.*` | No | `organism`, `max_genes`, `timeout`, `max_retries`, `tool` — NCBI Gene ESearch/ESummary lookup parameters |
| `deepinfra.*` / `.env: DEEPINFRA_API_KEY` | No | Optional alternate LLM provider; unset means Ollama is used |
| `paths.chat_dir` / `COPILOT_CHAT_DIR` | No | Chat session path |
| `paths.workdir_base` / `COPILOT_WORKDIR_BASE` | No | Working directory base path |
| `paths.tmp_base` / `COPILOT_TMP_BASE` | No | Temporary upload/OME-TIFF cache path |
| `paths.status_dir` / `COPILOT_STATUS_DIR` | No | Upload status JSON path |
| `paths.workspace_map` / `COPILOT_WORKSPACE_MAP` | No | Workspace-to-port map path |
| `paths.tutorial_image` / `COPILOT_TUTORIAL_IMAGE` | No | Local tutorial OME-TIFF path |
| `.env: PUBMED_API_KEY` | No | Optional NCBI key; changes the client limit from 3 to 10 request starts/second |
| `.env: PUBMED_EMAIL` | No | Developer contact recommended by NCBI |
| `.env: PUBMED_TOOL` | No | Registered E-utilities tool name; defaults to `spatial_omics_copilot` |
| `.env: PUBMED_CHROMA_DIR` | No | Persistent Chroma index path; defaults to `data/pubmed_chroma` |

## 8. Technical Risks

| **Risk** | **Mitigation** |
| -- | -- |
| Gigapixel image OOM | pyvips streaming — never load full image into RAM |
| LLM hallucinating citations | Only cite PMIDs returned by PubMed tool |
| PubMed rate limit or outage | Shared rate limiter, bounded Retry-After/backoff, optional `PUBMED_API_KEY`, and safe empty results |
| Real gene-annotation/pathway/PubMed calls run synchronously in the request path, ahead of the background job queue | Accepted risk — hasn't caused a real problem in practice (calls are fast and reliable so far); would need a background-execution boundary with timeout/retry if a slow or hanging external call ever blocks a request |
| A tool's genuine connection failure could look identical to "found nothing" | Fixed — all three tools (pathway, gene annotation, PubMed) distinguish a real failure (their status message contains "unavailable") from a successful-but-empty result, reporting the former as an error instead of a clean checkmark |
| Wrong disease/tissue context silently produces confident, well-formed results for the wrong sample | Mitigated for the LLM's own reasoning (disease context is extracted from the conversation and only ever stated when actually known, never asserted as a guess) — but PubMed's query still falls back to a fixed default (`"colorectal cancer"`) when nothing has been extracted yet, so it can still anchor on the wrong disease if the sample is never mentioned in chat |
| External literature query discloses derived research terms | Use HTTPS POST; disclose that genes/pathways/disease are sent to NCBI and add consent/opt-out in the UI |
| Retrieved abstracts/gene summaries are untrusted external text in an LLM prompt | Fenced as data with explicit delimiters; the model is instructed not to follow directives found inside them |
| LangGraph infinite loop | Enforced max 5 tool calls per turn (`copilot_agent.max_tool_calls`) |
| Session file corruption | Catch JSON errors; start fresh session |
| OME-TIFF conversion slow | Async conversion with progress bar |
| Response latency grows with conversation length | Conversation history sent to the model is bounded (last 4 turns) — unbounded history was tried and reverted, since Ollama re-processes the whole prompt from scratch on every call with no cross-call KV-cache reuse |
