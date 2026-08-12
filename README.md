# Spatial Omics Copilot

An AI-powered copilot for spatial transcriptomics research. Researchers load a
gigapixel histopathology image, draw a region of interest (ROI) on the tissue,
and immediately receive biologically grounded, literature-backed interpretations
— without leaving the visualization tool.

## Features

- Interactive gigapixel whole-slide image viewer with ROI drawing.
- Gene expression overlay from h5ad spatial transcriptomics data.
- Top differentially expressed genes extracted from any selected tissue region.
- Prototype pathway enrichment over GO/KEGG-labeled gene sets.
- Live NCBI PubMed ESearch/EFetch retrieval with bounded retries and no unrelated result padding; a ChromaDB semantic-search API is ready for agent integration.
- RAG pipeline with a LangGraph-compatible entry point and sequential fallback.
- Streaming Ollama chat interface for follow-up questions about selected regions; this is the conversational UI for the copilot, not a general-purpose medical chatbot.

## Supported Data Formats

```text
Whole-slide image:  .tiff, .ome.tiff, .svs
Gene expression:    .h5ad
```

### Convert 10x Visium HD Feature Slice H5

The app upload accepts `.h5ad`. If you have a 10x Visium HD
`feature_slice.h5`, convert it first:

```bash
python src/convert_feature_slice_h5.py \
  Visium_HD_Human_Colon_Cancer_feature_slice.h5 \
  Visium_HD_Human_Colon_Cancer_feature_slice.h5ad
```

By default, the converter bins 2 um feature-slice data into 16 um bins
(`--binning-scale 8`) and writes sparse AnnData with `obsm["spatial"]`.
Upload the generated `.h5ad` file in the Gene Expression Matrix box.

## Configuration

General app settings live in `config/app.yaml`.

```yaml
ollama:
  host: "http://localhost:11434"
  model: "qwen2.5vl:7b"
  vision_model: "qwen2.5vl:7b"
  timeout: 120
  num_predict: 220
  keep_alive: "10m"
  warmup: true          # load the model at startup instead of on first message

paths:
  chat_dir: "data/chat_sessions"
  status_dir: "data/status_data"
  workspace_map: "data/workspace_map.json"
  workdir_base: "tmp_data/workdirs"
  tmp_base: "tmp_data"
  tutorial_image: "tutorial/loki_tutorial_hskin_melanoma_downsampled.ome.tif"

app:
  hot_reload: false
```

Secrets stay in a local `.env` file. Use `.env.example` as a starting point.

```bash
PUBMED_API_KEY=...                         # optional; enables NCBI's 10 req/s tier
PUBMED_EMAIL=developer@example.org         # recommended by NCBI
PUBMED_TOOL=spatial_omics_copilot
PUBMED_CHROMA_DIR=data/pubmed_chroma
```

Environment variables can override YAML settings for deployment. Common
overrides include `OLLAMA_HOST`, `OLLAMA_MODEL`, `COPILOT_CHAT_DIR`,
`COPILOT_STATUS_DIR`, `COPILOT_WORKSPACE_MAP`, `COPILOT_WORKDIR_BASE`,
`COPILOT_TMP_BASE`, `COPILOT_TUTORIAL_IMAGE`, `COPILOT_HOT_RELOAD`, and the
four PubMed variables above.

Live literature retrieval needs outbound HTTPS access to NCBI. The client
limits itself to 3 requests/second without an API key and 10 requests/second
with one. ChromaDB is loaded only when semantic search is requested; its
default embedding model may be downloaded on first use.

PubMed records are supplied by NCBI/NLM. Review the
[NCBI disclaimer and copyright notice](https://www.ncbi.nlm.nih.gov/home/about/policies/)
before redistributing abstracts or using the service outside this class
prototype.

Calling the live backend sends the selected gene symbols, pathway labels,
disease context, and configured developer contact to NCBI over HTTPS (using
POST); it does not send the tissue image or expression matrix. The final UI
integration must disclose this external request and provide the required
consent/opt-out before enabling live retrieval for sensitive research data.

## Ollama Setup

The default local model provider is Ollama. Install and start Ollama before
launching the app if you want local chat responses.

Install Ollama:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Start the Ollama server:

```bash
ollama serve
```

In another terminal, pull the default vision-language model (the chat UI
selects this one by default so it can inspect ROI crops/images directly).
This model is heavier and can be slow on CPU-only machines:

```bash
ollama pull qwen2.5vl:7b
```

Optional: pull the smaller, faster text-only model too if you want a quick
option for turns that don't need image input.

```bash
ollama pull qwen2.5:0.5b
```

Verify Ollama is reachable:

```bash
ollama list
curl http://localhost:11434/api/tags
```

To change the Ollama host or default model, edit `config/app.yaml`:

```yaml
ollama:
  host: "http://localhost:11434"
  model: "qwen2.5vl:7b"
  vision_model: "qwen2.5vl:7b"
```

The chat UI includes a heavier vision model (`qwen2.5vl:7b`, selected by
default) and a faster text-only model (`qwen2.5:0.5b`); make sure the
selected model has been pulled locally.

## Quick Start

Python 3.11 is recommended. `pyvips` and the native `libvips` library are
required for OME-TIFF pyramid generation. Installing Python and `libvips` in a
single transaction avoids an extra Conda dependency-solving pass.

```bash
conda create -n spatial-copilot -c conda-forge \
  python=3.11 libvips=8.18.2 pip --solver=libmamba -y
conda activate spatial-copilot

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps

cp .env.example .env
spatial-copilot --port 8081 --workspace demo
```

The final editable install uses `--no-deps` because `requirements.txt` has
already installed the application dependencies. This prevents pip from
resolving the same dependency tree twice.

Open `http://localhost:8081/workspaces/demo` in your browser.

For local development, the app can also be launched directly:

```bash
python app/app.py --port 8081 --workspace demo
```

## Demo Dataset

- VisiumHD CytAssist Gene Expression — Human Colorectal Cancer (CRC), 10x Genomics
  https://www.10xgenomics.com/datasets/visium-hd-cytassist-gene-expression-libraries-of-human-crc

## Project Structure

```text
spatial-omics-copilot/
├── README.md
├── pyproject.toml
├── requirements.txt
├── config/
│   └── app.yaml                  # general app settings
├── app/                         # Dash/Flask application layer
│   ├── app.py                   # Dash entry point + callbacks
│   ├── layout.py                # Dash UI layout
│   ├── routes.py                # Flask HTTP routes
│   ├── worker.py                # background job queue + LLM streaming
│   ├── inference.py             # Ollama API wrapper
│   ├── config.py                # config/app.yaml + env var resolution
│   ├── session.py               # chat session read/write
│   ├── image_utils.py           # ROI crop, OME-TIFF caching
│   ├── status_store.py          # upload progress tracking
│   ├── utils.py                 # shared utilities
│   └── assets/
│       ├── chat.js              # chat UI: AGENT TRACE, pathway/DEG panels
│       ├── chat.css             # chat panel styling
│       ├── opioid.css           # main application layout styling
│       ├── s3_upload.js         # browser-side upload handling
│       ├── spinner.css          # loading state styling
│       ├── upload_styles.css    # upload drop zone/progress styling
│       ├── tutorial.js          # tutorial button behavior
│       ├── tutorial.css         # tutorial controls styling
│       └── font.css             # font imports
├── src/                         # installable Python packages
│   ├── niceview/                # UI/domain helpers
│   │   ├── interface/
│   │   │   ├── upload.py        # image + h5ad upload handlers
│   │   │   ├── visualization.py # spot overlay, VivViewer setup
│   │   │   ├── actions.py       # re-visualize, save ROI
│   │   │   ├── callback.py      # Dash callbacks
│   │   │   ├── interface.py     # shared interface state
│   │   │   └── data_io.py       # session data helpers
│   │   ├── pyplot/
│   │   │   └── leaflet.py       # VivViewer component builder + cluster legend
│   │   └── utils/               # io, colors, dataset, aristotle helpers
│   └── rag/                     # analysis pipeline
│       ├── contracts.py         # shared result types (PreprocessResult, ClusterResult,
│       │                        # ROISelection, ROIImageResult, DEGResult, AgentResult, ...)
│       ├── pipeline.py          # run_integration_pipeline(): preprocess -> cluster ->
│       │                        # ROI resolution -> DEG -> annotation -> pathway -> PubMed -> agent
│       ├── preprocessing.py     # QC, normalize, HVG, PCA (cached to disk)
│       ├── clustering.py        # Leiden / KMeans spatial clustering (cached to disk)
│       ├── deg/                 # DEG: Wilcoxon rank-sum + BH correction
│       ├── pathway_enrichment/  # real GO / KEGG ORA via gseapy/Enrichr
│       ├── gene_annotation/     # NCBI Gene functional annotation retrieval
│       ├── pubmed_retrieval/    # NCBI E-utilities retrieval + Chroma semantic search
│       ├── copilot_agent/       # the real LangGraph agent: dynamic tool routing,
│       │                        # multimodal prompt, DeepInfra client, disease-context
│       │                        # extraction hooks
│       ├── agent/               # back-compat re-export of copilot_agent.run_agent
│       ├── pathway/              # back-compat import path for pathway_enrichment
│       └── pubmed/               # back-compat import path for pubmed_retrieval
│   └── tests/                    # niceview/app-layer tests (upload, clustering, DEG, session, ...)
├── tests/                        # RAG-layer tests (agent, pathway, pubmed, gene annotation, e2e pipeline)
├── packages/
│   └── dash_viv_viewer/         # VivViewer React component package
├── data/                        # local runtime files
│   ├── demo/                    # 10x Visium HD Human Colon Cancer demo dataset
│   ├── chat_sessions/
│   ├── status_data/
│   └── workspace_map.json       # generated at runtime
└── docs/
    ├── PRD.md
    ├── specs.md
    ├── tech.md
    ├── rules.md
    ├── tickets.md
    ├── validation/               # per-person biological/functional validation notes
    └── planning/
```

## More Docs

- [Product Requirements](docs/PRD.md)
- [Specifications](docs/specs.md)
- [Technical Design](docs/tech.md)
- [Rules](docs/rules.md)
- [Tickets](docs/tickets.md)
