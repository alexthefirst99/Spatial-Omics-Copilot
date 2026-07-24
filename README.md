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
  model: "qwen2.5:0.5b"
  vision_model: "qwen2.5vl:7b"
  timeout: 120
  num_predict: 48
  keep_alive: "10m"

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

In another terminal, pull the default fast text model:

```bash
ollama pull qwen2.5:0.5b
```

Optional: pull the vision-language model only if you want the chat model to
inspect ROI crops/images directly. This model is much heavier and can be slow
on CPU-only machines.

```bash
ollama pull qwen2.5vl:7b
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
  model: "qwen2.5:0.5b"
  vision_model: "qwen2.5vl:7b"
```

The chat UI includes a fast text model (`qwen2.5:0.5b`) and a heavier vision
model (`qwen2.5vl:7b`); make sure the selected model has been pulled locally.

## Quick Start

Python 3.11 is recommended. pyvips is required for OME-TIFF pyramid generation.

```bash
conda create -n spatial-copilot python=3.11 -y
conda activate spatial-copilot
conda install -c conda-forge libvips
pip install -r requirements.txt   
pip install -e .
cp .env.example .env              
spatial-copilot --port 8081 --workspace demo
```

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
│       ├── pipeline.py          # fallback sequential pipeline (_run_sequential)
│       ├── preprocessing.py     # QC, normalize, PCA
│       ├── clustering.py        # Leiden / KMeans spatial clustering
│       ├── deg/                 # DEG computation
│       ├── pathway/             # GO / KEGG enrichment
│       ├── pubmed_retrieval/    # NCBI retrieval + Chroma semantic search
│       ├── pubmed/              # compatibility import for current pipeline
│       └── agent/               # run_agent entry point + prompt/tool wiring
├── packages/
│   └── dash_viv_viewer/         # VivViewer React component package
├── data/                        # local runtime files
│   ├── chat_sessions/
│   ├── status_data/
│   └── workspace_map.json       # generated at runtime
└── docs/
    ├── PRD.md
    ├── specs.md
    ├── tech.md
    ├── rules.md
    ├── tickets.md
    └── planning/
```

## More Docs

- [Product Requirements](docs/PRD.md)
- [Specifications](docs/specs.md)
- [Technical Design](docs/tech.md)
- [Rules](docs/rules.md)
- [Tickets](docs/tickets.md)
