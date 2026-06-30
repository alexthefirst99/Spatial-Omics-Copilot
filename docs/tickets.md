# Implementation Tickets: Spatial Omics Copilot

| **Team** | **Project Type** | **Date** |
| --- | --- | --- |
| Group | Class prototype | June 2026 |

## Milestone 1: Project Setup ✓ (Alex — done)

| **ID** | **Task** | **Status** |
| --- | --- | --- |
| T-001 | UI — VivViewer, image upload, h5ad upload, spot overlay, ROI drawing | ✓ Done |
| T-002 | Infrastructure — routes.py, worker.py, inference.py, session.py | ✓ Done |
| T-003 | Preprocessing — QC, normalize, HVG, PCA (`rag/preprocessing.py`) | ✓ Done |
| T-004 | Clustering — Leiden / KMeans, saves cluster JSON (`rag/clustering.py`) | ✓ Done |
| T-005 | Mock RAG pipeline — sequential fallback (`rag/pipeline.py`) | ✓ Done |
| T-006 | Chat UI — AGENT TRACE card, pathway bar chart, DEG bar chart | ✓ Done |
| T-007 | Folder structure — `rag/deg/`, `rag/pathway/`, `rag/pubmed/`, `rag/agent/` | ✓ Done |

## Milestone 2: DEG Extraction (rag/deg/)

| **ID** | **Task** | **Done When** |
| --- | --- | --- |
| T-008 | Add Wilcoxon rank-sum test to `_rank_high_expression_genes()` | `pvalue` field present in each gene dict |
| T-009 | Add Benjamini-Hochberg correction | `adj_pvalue` field present; genes filtered to adj_pvalue < 0.05 |
| T-010 | Pre-filter candidates before Wilcoxon (performance) | Runs in < 10s for a 3000-spot dataset |
| T-011 | Write `test_deg.py` | Tests cover cluster selection, ROI selection, empty selection |

## Milestone 3: Pathway Enrichment (rag/pathway/)

| **ID** | **Task** | **Done When** |
| --- | --- | --- |
| T-012 | Replace mock with real gseapy.enrichr() or g:Profiler API | Returns real GO/KEGG terms with valid adjusted p-values |
| T-013 | Handle empty gene list and API errors | Returns `[]` without raising an exception |
| T-014 | Write `test_pathway.py` | Tests cover happy path, empty input, API unavailability |

## Milestone 4: PubMed Retrieval (rag/pubmed/)

| **ID** | **Task** | **Done When** |
| --- | --- | --- |
| T-015 | Implement NCBI esearch + efetch calls | Returns real abstracts for a gene list |
| T-016 | Build query string from genes and pathways | Query combines gene symbols and pathway names |
| T-017 | Handle rate limiting and empty results | Returns `[]` without error; respects API rate limits |
| T-018 | Add vector store for semantic search (chromadb or faiss) | Fetched abstracts are embedded and searchable |
| T-019 | Write `test_pubmed.py` | Tests cover happy path, empty result, API unavailability |

## Milestone 5: LangGraph Agent (rag/agent/)

| **ID** | **Task** | **Done When** |
| --- | --- | --- |
| T-020 | Define LangChain tools in `tools.py`: `pathway_tool` and `pubmed_tool` | Agent can call both tools (DEG is not a tool — it runs automatically before the agent) |
| T-021 | Implement real LangGraph agent in `graph.py` | Agent dynamically decides which tools to call based on the message |
| T-022 | Dynamic `trace` field | `metadata.trace` reflects what the agent actually called, not a fixed list |
| T-023 | Add max-iteration guard | Agent stops after 5 tool calls |
| T-024 | Write `test_agent.py` | Agent calls at least one tool and returns complete output dict |

## Milestone 6: Prompt Engineering (rag/agent/prompt.py)

| **ID** | **Task** | **Done When** |
| --- | --- | --- |
| T-025 | Improve `build_prompt_context()` with domain framing | LLM identifies cell type, pathway activity, and clinical relevance |
| T-026 | Add structured citation instructions | LLM cites papers inline as [1], [2] consistently |
| T-027 | Test prompts against demo dataset | Responses are biologically relevant for the demo data |

## Milestone 7: Demo Data & Evaluation

| **ID** | **Task** | **Done When** |
| --- | --- | --- |
| T-028 | Download and validate spatial omics demo dataset | h5ad loads, spots overlay correctly, clustering runs |
| T-029 | End-to-end test with real data | Draw ROI → DEG → pathway → PubMed → streamed answer with citations |
| T-030 | Evaluate biological relevance of outputs | Genes and pathways make sense for the tissue type |
| T-031 | Record demo video | Shows ROI selection, AGENT TRACE, tool calls, streamed response |
| T-032 | Freeze final submission | Repo, docs, and demo complete |
