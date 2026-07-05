# Spatial Omics Copilot

An AI-powered copilot for spatial transcriptomics research. Researchers load a
gigapixel histopathology image, draw a region of interest (ROI) on the tissue,
and immediately receive biologically grounded, literature-backed interpretations
— without leaving the visualization tool.

## Features

- Interactive gigapixel whole-slide image viewer with ROI drawing.
- Gene expression overlay from h5ad spatial transcriptomics data.
- Top differentially expressed genes extracted from any selected tissue region.
- Biological pathway enrichment via GO and KEGG.
- PubMed literature retrieval grounded in the selected genes and pathways.
- Agentic RAG pipeline (LangGraph) that reasons over spatial context and literature.
- Streaming chat interface for follow-up questions about selected regions.

## Supported Data Formats

```text
Whole-slide image:  .tiff, .ome.tiff, .svs
Gene expression:    .h5ad
```

## Configuration

Create a local `.env` file.

### Optional

```bash
OLLAMA_MODEL=...             # override Ollama model (default: qwen3-vl:30b)
OLLAMA_HOST=...              # override Ollama server URL (default: http://localhost:11435)
OPENAI_API_KEY=...           # enables OpenAI models (e.g. gpt-4o)
PUBMED_API_KEY=...           # higher PubMed rate limit (optional but recommended)
COPILOT_CHAT_DIR=...         # path to store chat sessions (default: ./data/chat_sessions)
COPILOT_STATUS_DIR=...       # path to store upload status files (default: ./data/status_data)
COPILOT_WORKSPACE_MAP=...    # workspace-to-port map path (default: ./data/workspace_map.json)
COPILOT_WORKDIR_BASE=...     # path to store working directories (default: ~/copilot_workdirs)
```

## Quick Start

Python 3.11 is recommended. pyvips is required for OME-TIFF pyramid generation.

```bash
conda create -n spatial-copilot python=3.11 -y
conda activate spatial-copilot
conda install -c conda-forge libvips
pip install -r requirements.txt   # includes -e ./packages/dash_viv_viewer
pip install -e .
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
├── setup.py                      # compatibility shim for older tooling
├── app/                         # Dash/Flask application layer
│   ├── app.py                   # Dash entry point + callbacks
│   ├── layout.py                # Dash UI layout
│   ├── routes.py                # Flask HTTP routes
│   ├── worker.py                # background job queue + LLM streaming
│   ├── inference.py             # Ollama / OpenAI API wrapper
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
│       ├── pubmed/              # PubMed retrieval
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
