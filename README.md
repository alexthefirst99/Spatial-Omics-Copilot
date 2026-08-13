# Spatial Omics Copilot

<!--
Architecture diagram goes here.

Example:
<p align="center">
  <img src="docs/assets/architecture.png" alt="Spatial Omics Copilot architecture" width="900">
</p>
-->

Spatial Omics Copilot is a research app for exploring spatial transcriptomics together with pathology images.

The basic workflow is:

```text
ROI / cluster
    → differential-expression genes
    → agent router
    → NCBI Gene / Enrichr / PubMed
    → grounded LLM response
```

You can load a whole-slide image and a matching `.h5ad` file, select a region of interest, inspect genes, and ask questions about gene function, pathways, and supporting literature.

> This is a research prototype, not a clinical decision-support tool.

---

## What it supports

- Whole-slide pathology images
- Spatial gene-expression overlays
- Polygon, rectangle, and point ROIs
- Differential-expression analysis
- NCBI Gene lookup
- Enrichr pathway enrichment
- PubMed retrieval
- Local Ollama or hosted DeepInfra models
- Reproducible evaluation and test scripts

---

## Requirements

- macOS or Linux
- Conda / Miniforge
- Python 3.11
- `libvips` 8.18.2 or compatible
- Internet access for NCBI, Enrichr, PubMed, or DeepInfra
- Ollama only if you want to run the LLM locally

Supported input files:

| Data | Format |
|---|---|
| Pathology image | `.tif`, `.tiff`, `.ome.tif`, `.ome.tiff`, `.svs` |
| Spatial expression | `.h5ad` |

Run the commands below from the repository root.

---

## 1. Set up the environment

Create the Conda environment:

```bash
conda create -n spatial-copilot -c conda-forge \
  python=3.11 libvips=8.18.2 pip setuptools wheel \
  --solver=libmamba -y
```

Activate it:

```bash
conda activate spatial-copilot
```

Check Python:

```bash
python --version
```

It should show Python 3.11.

Install the project:

```bash
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e . --no-deps
python -m pip install -r requirements.txt
```

Create your local environment file:

```bash
cp .env.example .env
```

If the editable install fails with a `_distutils_hack` `AssertionError`, use:

```bash
SETUPTOOLS_USE_DISTUTILS=stdlib python -m pip install -e . --no-deps
```

---

## 2. Put the data in place

For local development, the simplest layout is:

```text
data/
└── demo/
    ├── sample.ome.tif
    └── sample.h5ad
```

For example:

```text
data/demo/Visium_HD_Human_Colon_Cancer_image.tif
data/demo/Visium_HD_Human_Colon_Cancer_feature_slice.h5ad
```

The image and `.h5ad` file must come from the **same tissue sample**.

You do not have to hard-code these paths into the app. Once the server is running, upload/select the image and `.h5ad` file from the workspace UI.

If your 10x data is `feature_slice.h5`, convert it with the script in `src`:

```bash
python src/convert_feature_slice_h5.py \
  data/demo/Visium_HD_Human_Colon_Cancer_feature_slice.h5 \
  data/demo/Visium_HD_Human_Colon_Cancer_feature_slice.h5ad
```

Then start the server and select the matching image and generated `.h5ad` in the workspace UI. Conversion may take several minutes.

---

## 3. Configure the LLM

Choose either Ollama or DeepInfra.

### Ollama

Start Ollama:

```bash
ollama serve
```

Keep that terminal open.

In another terminal:

```bash
ollama pull qwen2.5vl:7b
ollama list
```

In `.env`:

```env
LLM_PROVIDER=ollama
```

Optional overrides:

```env
OLLAMA_HOST=
OLLAMA_MODEL=
OLLAMA_VISION_MODEL=
```

### DeepInfra

In `.env`:

```env
LLM_PROVIDER=deepinfra
DEEPINFRA_API_KEY=your_api_key
DEEPINFRA_MODEL=Qwen/Qwen2.5-VL-7B-Instruct
```

`DEEPINFRA_TOKEN` can be used instead of `DEEPINFRA_API_KEY`.

### PubMed

Recommended `.env` settings:

```env
PUBMED_API_KEY=
PUBMED_EMAIL=
PUBMED_TOOL=
PUBMED_CHROMA_DIR=data/pubmed_chroma
```

`PUBMED_API_KEY` is optional. `PUBMED_EMAIL` is recommended for NCBI requests.

---

## 4. Run the app

Activate the environment:

```bash
conda activate spatial-copilot
```

Start the app:

```bash
spatial-copilot --port 8081 --workspace demo
```

Then open:

```text
http://localhost:8081/workspaces/demo
```

You can also run it directly with Python:

```bash
python app/app.py --port 8081 --workspace demo
```

For development:

```bash
python app/app.py --port 8081 --workspace demo --hot-reload
```

---

## 5. Use the workspace

Once the page opens:

1. Upload/select the pathology image.
2. Upload/select the matching `.h5ad` file.
3. Wait for the slide and spatial data to load.
4. Draw an ROI or select a cluster.
5. Open the gene panel.
6. Ask the copilot a question.

Examples:

```text
What genes are highly expressed in this ROI?
```

```text
What are the top differentially expressed genes in this region?
```

```text
What pathways are associated with these genes?
```

```text
What does the literature say about these genes?
```

---

## 6. Run the evaluation

### One-command full metrics

With the default app data in `data/demo` and provider credentials/model set in
`.env`, run:

```bash
./run_full_metrics
```

This validates the default `.h5ad` and `.tif` assets, automatically uses the
provider/model selected in `.env`, runs all 28 benchmark cases with generation,
and writes everything to `evaluation_outputs_full/`. The main machine-readable
result is `evaluation_outputs_full/full_metrics.json`; the directory also
contains category metrics, raw results, the readable summary, and human-review
sheets.

The audited 28-case benchmark uses controlled synthetic ROI fixtures. The demo
assets are preflighted and recorded in `dataset_manifest.json`, but they are not
presented as real-ROI biological ground truth because the demo directory does
not contain audited ROI polygons or labels.

For route/tool metrics without LLM generation:

```bash
./run_full_metrics --no-generation
```

### Advanced commands

Run the benchmark with the provider configured in `.env`:

```bash
python -m evaluation.runner \
  --config evaluation/eval_cases.json \
  --output-dir evaluation_outputs
```

Run with DeepInfra explicitly:

```bash
python -m evaluation.runner \
  --config evaluation/eval_cases.json \
  --output-dir evaluation_outputs \
  --provider deepinfra \
  --model Qwen/Qwen2.5-VL-7B-Instruct
```

To test routing and retrieval without an LLM call:

```bash
python -m evaluation.runner \
  --config evaluation/eval_cases.json \
  --output-dir evaluation_outputs \
  --no-generation
```

Results are written to:

```text
evaluation_outputs/
```

Main outputs:

| File | What it contains |
|---|---|
| `raw_results.jsonl` | Full result for each case |
| `metrics_summary.csv` | Automatic metrics |
| `evaluation_summary.md` | Readable benchmark summary |
| `human_review.csv` | Manual scientific review sheet |
| `business_metrics_review.csv` | Manual workflow review sheet |

The included benchmark uses synthetic workflow cases. It is not a clinical benchmark.

---

## 7. Run tests

Run everything:

```bash
python -m pytest -q
```

Useful focused tests:

```bash
python -m pytest tests/test_evaluation_runner.py -q
python -m pytest tests/test_agent.py -q
```

The normal test suite uses mocks and does not call paid APIs.

---

## 8. Reset local state

Use this if an old upload, chat session, or workspace state is causing problems.

Stop the app first, then run:

```bash
rm -rf tmp_data

find data/chat_sessions -mindepth 1 ! -name .gitkeep -exec rm -rf {} +
find data/status_data -mindepth 1 ! -name .gitkeep -exec rm -rf {} +

rm -f data/workspace_map.json
```

This clears runtime state but keeps files under:

```text
data/demo/
```

Start the app again:

```bash
spatial-copilot --port 8081 --workspace demo
```

---

## Repository layout

```text
Spatial-Omics-Copilot/
├── app/                        # Dash/Flask app
├── config/
│   └── app.yaml                # application defaults
├── data/
│   ├── demo/                   # put local demo/sample data here
│   ├── chat_sessions/          # generated runtime state
│   └── status_data/            # generated runtime state
├── evaluation/                 # benchmark and evaluation code
├── packages/
│   └── dash_viv_viewer/        # whole-slide viewer
├── src/
│   ├── rag/                    # routing, tools, retrieval, RAG
│   └── niceview/               # upload and visualization helpers
├── tests/
├── docs/
├── requirements.txt
├── pyproject.toml
└── README.md
```
