# Implementation Tickets: Spatial Omics Copilot

| **Team** | **Project Type** | **Date** |
| --- | --- | --- |
| Group | Class prototype | June 2026 |

## Phase 1: Project Setup ✓ (Alex — done)

| **ID** | **Task** | **Status** |
| --- | --- | --- |
| T-001 | UI — VivViewer, image upload, h5ad upload, spot overlay, ROI drawing | ✓ Done |
| T-002 | Infrastructure — routes.py, worker.py, inference.py, session.py | ✓ Done |
| T-003 | Preprocessing — QC, normalize, HVG, PCA (`src/rag/preprocessing.py`) | ✓ Done |
| T-004 | Clustering — Leiden / KMeans, saves cluster JSON (`src/rag/clustering.py`) | ✓ Done |
| T-005 | Mock RAG pipeline — sequential fallback (`src/rag/pipeline.py`) | ✓ Done |
| T-006 | Chat UI — AGENT WORKFLOW card, pathway bar chart, DEG bar chart | ✓ Done |
| T-007 | Folder structure — `src/rag/deg/`, `src/rag/pathway/`, `src/rag/pubmed/`, `src/rag/agent/` | ✓ Done |

## Phase 2: Preprocessing (src/rag/preprocessing.py) ✓ (Zainab — done)

| **ID** | **Task** | **Done When** | **Status** |
| --- | --- | --- | --- |
| T-033 | Add spot-level QC filtering — minimum genes per spot, maximum mitochondrial gene fraction | Spots below threshold removed before normalization; `min_genes` and `max_mito_pct` are configurable params | ✓ Done |
| T-034 | Store raw counts layer before normalization | `adata.layers["counts"]` present after preprocessing; DEG Wilcoxon test reads from it | ✓ Done |
| T-035 | Cache preprocessed adata to disk | Re-running on same h5ad skips preprocessing and loads cached result in < 1s | ✓ Done |
| T-036 | Write `test_preprocessing.py` | Tests cover: valid h5ad processes without error, missing spatial key raises ValueError, HVG selection reduces gene count, PCA components present in result | ✓ Done |

## Phase 3: Clustering (src/rag/clustering.py) ✓ (Zainab — done)

| **ID** | **Task** | **Done When** | **Status** |
| --- | --- | --- | --- |
| T-037 | Expose Leiden resolution as configurable parameter | `run_spatial_clustering()` accepts `resolution` kwarg; default 0.8; stored in cluster JSON | ✓ Done |
| T-038 | Add spatial coordinates as auxiliary features | PCA embedding is augmented with normalized x/y coordinates before clustering; spatially coherent clusters improve visually | ✓ Done |
| T-039 | Support user-specified cluster count | `run_spatial_clustering()` accepts `n_clusters` override; skips auto-estimation when provided | ✓ Done |
| T-040 | Cache cluster results | Skip re-clustering if cluster JSON already exists and h5ad modification time has not changed | ✓ Done |
| T-041 | Write `test_clustering.py` | Tests cover: Leiden runs on small h5ad, KMeans fallback triggers on Leiden failure, palette has correct number of colors, cluster JSON schema is valid | ✓ Done |

## Phase 4: DEG Extraction (src/rag/deg/) ✓ (Rodney — done)

| **ID** | **Task** | **Done When** | **Status** |
| --- | --- | --- | --- |
| T-008 | Add Wilcoxon rank-sum test to `_rank_high_expression_genes()` | `pvalue` field present in each gene dict | ✓ Done |
| T-009 | Add Benjamini-Hochberg correction | `adj_pvalue` field present; genes filtered to adj_pvalue < 0.05 | ✓ Done |
| T-010 | Pre-filter candidates before Wilcoxon (performance) | Runs in < 10s for a 3000-spot dataset | ✓ Done — pre-filter itself works, but this specific target is not met on the real ~137k-spot demo dataset: the reference group for the test stays the full remaining dataset regardless of ROI size, so the dominant cost is total spots × genes, not selection size. See `docs/validation/person2_deg_notes.md`. |
| T-011 | Write `test_deg.py` | Tests cover cluster selection, ROI selection, empty selection | ✓ Done |

## Phase 5: Pathway Enrichment (src/rag/pathway_enrichment/) ✓ (Quynh — done)

| **ID** | **Task** | **Done When** | **Status** |
| --- | --- | --- | --- |
| T-012 | Replace mock with a real Enrichr or g:Profiler API | Returns real GO/KEGG terms with valid adjusted p-values | ✓ Done |
| T-013 | Handle empty gene list and API errors | Returns `[]` without raising an exception | ✓ Done |
| T-014 | Write `test_pathway.py` | Tests cover happy path, empty input, API unavailability | ✓ Done |

### T-012 — Real Pathway Enrichment Backend

**Status:** Implemented by Quynh

**Current behavior:** `src/rag/pathway_enrichment/enrichment.py` runs real ORA against GO Biological Process and KEGG through Enrichr's HTTPS API. `src/rag/pathway/` is a back-compat import path for the current pipeline. The gene list is uploaded once, each configured library is fetched separately, and the results are merged. Transient failures are retried, and an unavailable tabular export falls back to Enrichr's JSON endpoint.

**Desired behavior (met):** Replace mock and synthetic pathway output with a documented real enrichment backend for GO and KEGG terms, such as `gseapy.enrichr()`, g:Profiler, Enrichr, or another backend selected by the team.

**Implementation notes:**
- Preserve `enrich_pathways(genes: list[str], top_n: int = 6) -> list[dict]` where possible.
- Preserve existing output keys: `name`, `gene_count`, `set_size`, `pvalue`, and `overlap`.
- Treat p-values as adjusted p-values when the backend provides FDR / BH values.
- Remove fake pathway padding once real enrichment is enabled.
- Keep backend configuration local-dev friendly and document any network/API requirements in the ticket implementation notes or follow-up docs.

**Acceptance criteria:**
- Empty input returns `[]`.
- No enriched terms returns `[]` instead of synthetic pathway bars.
- Successful enrichment returns real GO/KEGG pathway names and valid adjusted p-values sorted ascending.
- API/backend failures are caught and return `[]` without crashing callers.
- Existing RAG metadata formatting in `src/rag/pipeline.py` continues to work.

**Suggested tests:**
- `test_pathway_empty_gene_list_returns_empty()`
- `test_pathway_no_enrichment_returns_empty_without_padding()`
- `test_pathway_success_maps_backend_rows_to_public_schema()`
- `test_pathway_backend_failure_returns_empty()`

## Phase 6: PubMed Retrieval (src/rag/pubmed_retrieval/) ✓ (Anh — done)

| **ID** | **Task** | **Done When** | **Status** |
| --- | --- | --- | --- |
| T-015 | Implement NCBI esearch + efetch calls | Returns real abstracts for a gene list | ✓ Done |
| T-016 | Build query string from genes and pathways | Query combines gene symbols and pathway names | ✓ Done |
| T-017 | Handle rate limiting and empty results | Returns `[]` without error; respects API rate limits | ✓ Done |
| T-018 | Add vector store for semantic search (chromadb or faiss) | Fetched abstracts are embedded and searchable | ✓ Done |
| T-019 | Write `test_pubmed.py` | Tests cover happy path, empty result, API unavailability | ✓ Done |

**Implementation status:** T-015 through T-019 were implemented by Anh on
July 23, 2026. The focused suite is in `tests/test_pubmed.py`; live relevance
review notes are in `docs/validation/person4_pubmed_notes.md`.

### T-015 — Real PubMed / Literature Retrieval Backend

**Status:** Implemented by Anh on July 23, 2026

**Current behavior:** `src/rag/pubmed_retrieval/` builds a disease-constrained query, calls live NCBI ESearch and batched EFetch, parses real citation XML, applies bounded retry/rate limiting, and returns fewer results instead of padding. `src/rag/pubmed/` is a compatibility import for the current pipeline.

**Desired behavior:** Replace curated mock abstracts with live PubMed/NCBI retrieval or a documented retrieval backend that returns real literature records for the selected genes and pathway terms.

**Implementation notes:**
- Preserve `retrieve_abstracts(genes: list[str], pathways: list[str] | None = None, n: int = 3) -> list[dict]` where possible.
- Preserve existing output keys: `pmid`, `title`, `journal`, `year`, and `snippet`.
- Implement NCBI E-utilities `esearch` + `efetch` or document an equivalent backend.
- Use `PUBMED_API_KEY` when available and respect unauthenticated/authenticated rate limits.
- Return up to `n` relevant live PubMed results. If fewer relevant hits are available, return fewer results instead of padding with unrelated or mock papers.

**Acceptance criteria:**
- Normal retrieval returns real PubMed-backed abstracts with PMIDs.
- No-result queries return `[]` and do not silently substitute unrelated papers.
- Fewer-than-`n` live results are returned without unrelated padding and documented in the code/test names.
- API errors, timeouts, malformed responses, and missing API keys are handled without crashing callers.

**Suggested tests:**
- `test_pubmed_no_results_returns_empty()`
- `test_pubmed_fewer_than_n_results_behavior_is_documented()`
- `test_pubmed_success_maps_ncbi_records_to_public_schema()`
- `test_pubmed_backend_failure_returns_empty()`

### T-018 — Vector Store Integration for Literature Evidence

**Status:** Implemented by Anh on July 23, 2026

**Current behavior:** `semantic_search_abstracts()` lazily indexes the papers from the current `PubMedResult` in ChromaDB, scopes queries to that corpus, preserves PMID metadata, and returns metric-labelled similarity scores. Chroma failures safely return `[]`. `copilot_agent/tools.py`'s `run_pubmed_tool()` now calls it with the user's question when `semantic_rerank` is enabled (`copilot_agent.semantic_rerank` in config, off by default since Chroma's default embedding model downloads a ~79 MB ONNX model on first use).

**Desired behavior:** Implement or wire an optional vector store for semantic search over literature evidence if vector retrieval remains part of the desired RAG architecture.

**Implementation notes:**
- Choose and document the backend, such as Chroma, FAISS, or another local-friendly store.
- Clarify what is indexed: fetched PubMed abstracts, snippets, titles, pathway descriptions, or a combination.
- Store enough metadata to preserve PMID/citation provenance in responses.
- Make the vector store optional/configurable for local development and CI.
- Define rebuild behavior when abstracts change, cached data is missing, or the index schema changes.

**Acceptance criteria:**
- Fetched literature can be indexed and queried semantically.
- Retrieval returns records with stable source metadata, including PMID when available.
- Index persistence and rebuild behavior are deterministic and documented in implementation notes.
- Disabling the vector store leaves pathway/PubMed retrieval usable.
- Vector retrieval does not invent citations or detach snippets from source records.

**Suggested tests:**
- `test_vector_store_indexes_pubmed_records_with_metadata()`
- `test_vector_store_retrieves_relevant_record_for_query()`
- `test_vector_store_rebuilds_when_index_missing()`
- `test_vector_store_disabled_falls_back_without_error()`

## Phase 7: LangGraph Agent (src/rag/copilot_agent/ — src/rag/agent/ is now a back-compat import path) ✓ (JN — done)

| **ID** | **Task** | **Done When** | **Status** |
| --- | --- | --- | --- |
| T-020 | Define LangChain tools in `tools.py`: `pathway_tool` and `pubmed_tool` | Agent can call both tools (DEG is not a tool — it runs automatically before the agent) | ✓ Done — `gene_annotation_tool` was added too, beyond the original two |
| T-021 | Implement real LangGraph agent in `graph.py` | Agent dynamically decides which tools to call based on the message | ✓ Done |
| T-022 | Dynamic `workflow_steps` field | `metadata.workflow_steps` reflects what the agent actually called, not a fixed list | ✓ Done |
| T-023 | Add max-iteration guard | Agent stops after 5 tool calls | ✓ Done |
| T-024 | Write `test_agent.py` | Agent calls at least one tool and returns complete output dict | ✓ Done |

### T-021 — Agentic RAG and Dynamic Tool Selection

**Status:** Implemented by JN

**Current behavior:** `src/rag/copilot_agent/graph.py` is a real LangGraph state machine (`route` → `run_tool`, looped → `synthesize`) that decides which of gene_annotation_tool/pathway_tool/pubmed_tool to call based on the question — any combination, including none. `_run_sequential()` in `rag/pipeline.py` still exists as an explicit offline fallback when LangGraph is unavailable, per the implementation notes below.

**Desired behavior (met):** Replace the fixed sequential fallback with LangGraph or equivalent agentic routing so the agent decides whether to call pathway enrichment, PubMed retrieval, both, or neither based on the user query and available DEG/context inputs.

**Implementation notes:**
- Preserve `run_agent(gene_objects, message="", label="selection") -> dict`.
- Keep `_run_sequential()` only as an explicit fallback if it remains useful for local/offline operation.
- DEG remains pre-computed by the UI; do not move DEG into the agent as a tool unless the specs change.
- `metadata.workflow_steps` must reflect actual tool calls, not a hardcoded list.
- Maintain the max-tool-call / max-iteration guard from the desired architecture.

**Acceptance criteria:**
- Irrelevant/general chat queries can return a valid response context without unnecessary pathway or PubMed calls.
- Pathway-specific queries call pathway enrichment.
- Literature/citation-specific queries call PubMed retrieval.
- Questions that need both biological pathway context and literature can call both.
- The returned dict keeps `gene_objects`, `context_str`, and `metadata` compatible with `routes.py`, `worker.py`, and the chat UI.

**Suggested tests:**
- `test_agent_irrelevant_query_skips_pathway_and_pubmed_tools()`
- `test_agent_pathway_query_calls_pathway_tool_only_when_literature_not_needed()`
- `test_agent_literature_query_calls_pubmed_tool()`
- `test_agent_combined_query_calls_pathway_and_pubmed_tools()`
- `test_agent_trace_matches_actual_tool_calls()`
- `test_agent_respects_max_tool_call_guard()`

### T-024 — Agent Test Coverage

**Status:** Implemented by JN

**Current behavior:** `tests/test_agent.py` exists and covers the real routing behavior — irrelevant queries skip tools, pathway/literature-specific queries call the right tool, combined queries call multiple tools, trace matches actual calls, and the max-tool-call guard is enforced.

**Desired behavior (met):** Add focused agent tests that verify the public `run_agent()` contract and tool-selection behavior after T-021 is implemented.

**Implementation notes:**
- Mock pathway and PubMed tools so tests prove routing without requiring network calls.
- Keep separate tests for schema compatibility and routing decisions.
- Include fallback-mode tests only if `_run_sequential()` remains a supported fallback.

**Acceptance criteria:**
- `test_agent.py` or an equivalent clearly named agent test file exists.
- Tests fail if pathway/PubMed are called for irrelevant queries.
- Tests fail if pathway or literature queries skip the required tool.
- Tests verify trace, citations, pathways, and DEG metadata remain UI-compatible.

**Suggested tests:**
- `test_agent_output_schema_matches_routes_contract()`
- `test_agent_no_gene_objects_uses_demo_fallback_when_configured()`
- `test_agent_tool_errors_do_not_crash_turn()`

## Phase 8: Prompt Engineering (src/rag/copilot_agent/prompt.py) ✓ (JN — done)

| **ID** | **Task** | **Done When** | **Status** |
| --- | --- | --- | --- |
| T-025 | Improve `build_prompt_context()` with domain framing | LLM identifies cell type, pathway activity, and clinical relevance | ✓ Done — as `build_evidence_context()` |
| T-026 | Add structured citation instructions | LLM cites papers inline as [1], [2] consistently | ✓ Done |
| T-027 | Test prompts against demo dataset | Responses are biologically relevant for the demo data | ✓ Done — see `docs/validation/person5_prompt_notes.md` |

## Phase 9: Demo Data & Evaluation

| **ID** | **Task** | **Done When** | **Status** |
| --- | --- | --- | --- |
| T-028 | Download and validate spatial omics demo dataset | h5ad loads, spots overlay correctly, clustering runs | ✓ Done — 10x Visium HD Human Colon Cancer dataset in `data/demo/` |
| T-029 | End-to-end test with real data | Draw ROI → DEG → pathway → PubMed → streamed answer with citations | ✓ Done — verified live against the demo dataset, plus `tests/test_e2e_pipeline.py` |
| T-030 | Evaluate biological relevance of outputs | Genes and pathways make sense for the tissue type | ✓ Done — see `docs/validation/person2_deg_notes.md` and `docs/validation/person3_pathway_notes.md` |
| T-031 | Record demo video | Shows ROI selection, AGENT WORKFLOW, tool calls, streamed response | ✓ Done |
| T-032 | Freeze final submission | Repo, docs, and demo complete | Todo |
| T-044 | Replace domain-inappropriate demo fallback genes | Empty ROI/no h5ad paths clearly say no gene context, or use only explicitly labeled CRC/demo-dataset genes | ✓ Done |
| T-045 | Add automated test coverage for h5ad upload validation | `test_upload.py` exercises spatial-key validation, non-.h5ad rejection, and the empty-filenames no-op | ✓ Done — see `src/tests/test_upload.py` |

### T-044 — Remove or Relabel Demo Gene Fallback

**Status:** Done

**Current behavior:** No h5ad/empty ROI returns a clear "no gene expression data loaded" message; `test_agent_no_gene_objects_reports_no_data_instead_of_demo_genes` in `tests/test_agent.py` covers this directly. No fallback path substitutes unrelated demo genes as if they were real ROI evidence.

**Desired behavior:** Empty ROI or missing h5ad cases should not appear as real ROI-specific biology. The system should either show a clear no-gene-context message or use only explicitly labeled demo-mode genes appropriate for the selected demo dataset.

**Acceptance criteria:**
- No h5ad loaded returns a clear message instead of silently using demo genes.
- ROI with no spots returns an empty gene list and explanatory status.
- Demo fallback, if enabled, is explicitly labeled and not presented as ROI evidence.

## Phase 10: RAG Runtime Boundary and Test Hygiene

| **ID** | **Task** | **Done When** | **Status** |
| --- | --- | --- | --- |
| T-042 | Move real RAG API calls off the main request path | External pathway/PubMed/vector calls run in background work with timeout, retry, and user-visible status | Known limitation — not tracked as active work, see below |
| T-043 | Reconcile stale RAG test references | Missing/stale tests such as `test_agent.py` and `test_upload.py` are either added or tracked in a later docs cleanup | ✓ Done |

### T-042 — Async / Background Execution Boundary for Real RAG APIs

**Status:** Known limitation, accepted — not tracked as active work. `routes.py` calling the agent synchronously hasn't caused a real problem in practice (calls are fast and reliable so far); see `docs/tech.md` section 8. Revisit only if a slow or hanging external call actually blocks a request.

**Current behavior:** `routes.py` calls the agent (`run_copilot_agent()`) before enqueueing the chat job. This is acceptable for mock/fallback code but will block the request path once real PubMed, pathway, or vector APIs are enabled.

**Ideal behavior (not planned):** Move real external RAG calls out of the main Flask request path and align execution with the background-worker expectations in the architecture rules.

**Implementation notes:**
- Decide whether the agent call (`run_copilot_agent()`) runs inside `worker.py`, a dedicated RAG executor, or another background task abstraction.
- Return meaningful UI/API status while RAG retrieval and LLM generation are pending.
- Add timeouts, bounded retries, and clear failure messages for PubMed, enrichment, and vector-store operations.
- Preserve existing chat session persistence and streaming behavior.
- Avoid indefinitely blocking `/chat` responses when external services are slow.

**Acceptance criteria:**
- `/chat` does not synchronously wait on live PubMed/enrichment/vector API calls.
- RAG failures produce usable metadata/status and do not prevent a chat response from being recorded.
- Timeouts and retries are bounded and configurable.
- The UI can distinguish queued, processing, failed, and completed RAG states where needed.
- Existing mock/fallback mode remains usable for local demos.

**Suggested tests:**
- `test_chat_enqueue_does_not_block_on_slow_rag_backend()`
- `test_rag_background_timeout_records_failure_metadata()`
- `test_rag_background_retry_is_bounded()`
- `test_worker_injects_rag_context_after_background_retrieval()`

### T-043 — RAG Test Documentation Cleanup

**Status:** Done — `test_agent.py` now exists (T-024); `test_upload.py` now exists too (T-045).

**Current behavior:** `tests/test_agent.py` exists and covers routing behavior in full (see T-024). `src/tests/test_upload.py` exists and covers h5ad spatial-key validation, non-`.h5ad` rejection, and the empty-filenames no-op (see T-045) — general upload coverage also lives in `test_feature_slice_upload.py` (scoped to the h5 converter script) and `test_pipeline.py`/`test_inference.py`.

**Desired behavior:** Reconcile stale test references after the implementation tickets above are completed, without weakening the desired product/spec/rule targets.

**Implementation notes:**
- Do not modify `docs/rules.md` as part of this ticket unless that later task explicitly allows docs cleanup.
- Prefer adding the missing tests when the referenced behavior is still desired.
- If a test file has been intentionally renamed, update ticket/planning references in a dedicated docs-cleanup change.
- Keep implementation-gap tickets separate from docs-cleanup tickets.

**Acceptance criteria:**
- `test_agent.py` exists or ticket/planning docs consistently point to the actual agent test file.
- `test_upload.py` exists or ticket/planning docs consistently point to the actual upload test file.
- The distinction between desired target behavior and incomplete implementation remains clear.
- No PRD/spec/rules/tech behavior is downgraded to match current mocks.

**Suggested tests:**
- No runtime test required for the docs cleanup itself.
- Run the full existing pytest suite after adding or renaming tests.
