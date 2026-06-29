# Spatial Omics Copilot

An AI-powered copilot for spatial transcriptomics research. Researchers load a
gigapixel histopathology image, draw a region of interest (ROI) on the tissue,
and immediately receive biologically grounded, literature-backed interpretations
— without leaving the visualization tool.

## Features

- Interactive gigapixel whole-slide image viewer with ROI drawing.
- Gene expression overlay from h5ad spatial transcriptomics data.
- Top differentially expressed genes extracted from any selected tissue region.
- Biological pathway enrichment via KEGG/Reactome.
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

### Required

```bash
OLLAMA_MODEL=llama3          # or any Ollama-hosted model
```

### Optional

```bash
OPENAI_API_KEY=...           # enables OpenAI models instead of Ollama
ANTHROPIC_API_KEY=...        # enables Claude models
PUBMED_API_KEY=...           # higher PubMed rate limit (optional but recommended)
COPILOT_CHAT_DIR=...          # path to store chat sessions (default: ./chat_sessions)
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

pip install -r requirements.txt
pip install -e ./dash_viv_viewer
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
├── app/
│   ├── app.py              # entry point, Dash callbacks
│   ├── layout.py           # Dash UI layout
│   ├── routes.py           # Flask routes
│   ├── session.py          # session read/write with file locking
│   ├── inference.py        # Ollama / OpenAI streaming
│   ├── image_utils.py      # ROI crop, OME-TIFF caching
│   ├── worker.py           # background job queue
│   ├── status_store.py     # upload progress tracking
│   └── utils.py            # setup_work_dir
├── niceview/
│   ├── interface/          # upload, ROI extraction, actions, visualization
│   └── utils/              # dataset, io, colors, rendering, cell tools
├── rag/
│   ├── agent.py            # LangGraph agentic workflow (orchestrator)
│   ├── pubmed.py           # PubMed literature retrieval tool
│   ├── pathways.py         # KEGG/Reactome pathway enrichment tool
│   ├── vectorstore.py      # embedding store for cached abstracts
│   └── prompts.py          # synthesis prompt templates
├── dash_viv_viewer/        # VivViewer Dash component package
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
