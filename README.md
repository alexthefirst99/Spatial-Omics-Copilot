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

---

## What it supports

- Whole-slide pathology images
- Spatial gene-expression overlays
- Polygon, rectangle, and point ROIs
- Differential-expression analysis
- NCBI Gene lookup
- Enrichr pathway enrichment
- PubMed retrieval
- Per-turn agent trace cards for routed RAG questions
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
DEEPINFRA_MODEL=Qwen/Qwen2.5-VL-32B-Instruct
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

### Trace storage

Agent traces are displayed in the chat interface and saved with the corresponding
chat session. Session files are created automatically at runtime under
`data/chat_sessions/`.

Each RAG-enabled message stores its trace in `rag_metadata.trace`, allowing the
agent's routing, tool calls, and generation status to be inspected later.
---

## 6. Run the evaluation

The evaluator measures all **7 technical + 5 business metrics** on 10 real
ROIs from the loaded spatial-transcriptomics dataset. It does not use synthetic
genes or synthetic expression results. These metrics answer: **How well did the
system perform?**

Before running, confirm that these matching sample files exist:

```text
data/demo/Visium_HD_Human_Colon_Cancer_feature_slice.h5ad
data/demo/Visium_HD_Human_Colon_Cancer_image.tif
```

Configure a vision-capable model in `.env`. For example, with DeepInfra:

```env
LLM_PROVIDER=deepinfra
DEEPINFRA_API_KEY=your_api_key
DEEPINFRA_MODEL=Qwen/Qwen2.5-VL-32B-Instruct
```

Then run this one command from the repository root:

```bash
python -m evaluation.runner \
  --config evaluation/eval_cases.json \
  --output-dir evaluation_outputs
```

The configured provider/model is used for answer generation and structured LLM
judging. A vision-capable model is required to populate the image-to-gene
metric; otherwise that metric is reported as `N/A`, never fabricated.
Each ROI normally makes one answer-generation call, one structured text-judge
call, and one vision-judge call, so the complete live run can take several
minutes depending on the provider and external biomedical services.

The evaluator automatically builds 10 deterministic real ROIs:

- fixed random seed `42`;
- one saved ROI snapped to its nearest real expression spot;
- nine additional centers sampled only from real spatial spots;
- consistent 256×256-pixel square bounds;
- at least 100 spots per ROI, with invalid candidates resampled;
- real H&E crop and real production ROI-vs-rest DEG for every case.

The ROI set is cached in `evaluation_outputs/roi_fixtures.json` and reused only
when the dataset fingerprint and ROI settings still match.

Optional provider/model overrides:

```bash
python -m evaluation.runner \
  --config evaluation/eval_cases.json \
  --output-dir evaluation_outputs \
  --provider deepinfra \
  --model Qwen/Qwen2.5-VL-32B-Instruct
```

The command continues after an individual ROI failure and writes progress after
every ROI. Final outputs are:

| File | What it contains |
|---|---|
| `evaluation_outputs/summary.md` | Final tables with exactly 7 technical and 5 business metrics |
| `evaluation_outputs/technical_metrics.csv` | The 7 technical metrics |
| `evaluation_outputs/business_metrics.csv` | The 5 business metrics |
| `evaluation_outputs/per_roi_results.csv` | Auditable per-ROI metric inputs/results |
| `evaluation_outputs/raw_results.json` | ROI bounds, spot counts, crops, real DEG/evidence, answers, raw judge outputs, timings, errors, and supplementary per-ROI agent traces |

`raw_results.json` may include the recorded agent trace for each evaluated ROI
or case. This is supplementary diagnostic information for debugging,
inspection, and reproducibility; the trace is not itself an evaluation metric
or reported score.

This is a research evaluation, not clinical validation. External NCBI, Enrichr,
PubMed, and model responses can change between runs; ROI selection itself is
deterministic.

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
