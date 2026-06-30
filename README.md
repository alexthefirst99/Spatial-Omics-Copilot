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
COPILOT_CHAT_DIR=...         # path to store chat sessions (default: ./chat_sessions)
COPILOT_WORKDIR_BASE=...     # path to store working directories (default: ~/copilot_workdirs)
```

## Quick Start

Python 3.11 is recommended. pyvips is required for OME-TIFF pyramid generation.

```bash
conda create -n spatial-copilot python=3.11 -y
conda activate spatial-copilot

# system dependency for image processing
brew install vips            # macOS
# sudo apt-get install libvips-dev  # Ubuntu/Linux

pip install -r requirements.txt   # includes -e ./dash_viv_viewer
python app/app.py --port 8081 --token hello
```

Open `http://localhost:8081/app/hello` in your browser.

## Demo Dataset

- VisiumHD CytAssist Gene Expression — Human Colorectal Cancer (CRC), 10x Genomics
  https://www.10xgenomics.com/datasets/visium-hd-cytassist-gene-expression-libraries-of-human-crc

## Project Structure

```text
spatial-omics-copilot/
├── README.md
├── requirements.txt
├── setup.py
├── app/                         # infrastructure layer
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
├── niceview/                    # UI layer
│   ├── interface/
│   │   ├── upload.py            # image + h5ad upload handlers
│   │   ├── visualization.py     # spot overlay, VivViewer setup
│   │   ├── actions.py           # re-visualize, save ROI
│   │   ├── callback.py          # Dash callbacks
│   │   ├── interface.py         # shared interface state
│   │   └── data_io.py           # session data helpers
│   ├── pyplot/
│   │   └── leaflet.py           # VivViewer component builder + cluster legend
│   └── utils/                   # io, colors, dataset, aristotle helpers
├── rag/                         # analysis pipeline
│   ├── pipeline.py              # fallback sequential pipeline (_run_sequential)
│   ├── preprocessing.py         # QC, normalize, PCA
│   ├── clustering.py            # Leiden / KMeans spatial clustering
│   ├── deg/
│   │   ├── __init__.py          # exposes: get_roi/cluster_high_expression_genes
│   │   └── extraction.py        # DEG computation
│   ├── pathway/
│   │   ├── __init__.py          # exposes: enrich_pathways
│   │   └── enrichment.py        # ORA against GO / KEGG
│   ├── pubmed/
│   │   ├── __init__.py          # exposes: retrieve_abstracts
│   │   └── retrieval.py         # mock retrieval; target: NCBI E-utilities
│   └── agent/
│       ├── __init__.py          # exposes: run_agent  ← only public entry point
│       ├── graph.py             # LangGraph agent (currently mock)
│       ├── tools.py             # LangChain tool definitions
│       └── prompt.py            # context string formatting
├── dash_viv_viewer/             # VivViewer React component package
└── docs/
    ├── PRD.md
    ├── specs.md
    ├── tech.md
    ├── rules.md
    └── tickets.md
```

## More Docs

- [Product Requirements](docs/PRD.md)
- [Specifications](docs/specs.md)
- [Technical Design](docs/tech.md)
- [Rules](docs/rules.md)
- [Tickets](docs/tickets.md)
