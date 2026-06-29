# Implementation Tickets: Spatial Omics Copilot

| **Team** | **Project Type** | **Date** |
| --- | --- | --- |
| Group | Class prototype | June 2026 |

## Milestone 1: Project Setup

| **ID** | **Task** | **Priority** | **Done When** |
| --- | --- | --- | --- |
| T-001 | Create `rag/` folder with stub files | High | `rag/agent.py`, `rag/pubmed.py`, `rag/pathways.py`, `rag/vectorstore.py`, `rag/prompts.py` exist with placeholder functions |
| T-002 | Add RAG dependencies to requirements.txt | High | `langchain`, `langgraph`, `sentence-transformers`, `faiss-cpu` install cleanly |
| T-003 | Confirm existing app runs end-to-end | High | `python app/app.py --port 8081 --token hello` loads without errors |

## Milestone 2: PubMed Retrieval Tool

| **ID** | **Task** | **Priority** | **Done When** |
| --- | --- | --- | --- |
| T-004 | Implement `search_pubmed(query, max_results)` | High | Returns list of `{title, abstract, pmid, year}` dicts |
| T-005 | Build query string from gene names and pathways | Medium | Query combines gene symbols and pathway names into a meaningful PubMed search string |
| T-006 | Handle rate limiting and empty results | Medium | Returns empty list (not error) when no results found; respects API rate limit |
| T-007 | Write `test_pubmed.py` | High | Tests cover happy path, empty result, and API unavailability |

## Milestone 3: Pathway Enrichment Tool

| **ID** | **Task** | **Priority** | **Done When** |
| --- | --- | --- | --- |
| T-008 | Implement `get_pathways(genes)` via KEGG REST API | High | Returns `{pathways: [{name, id, p_value}], source}` |
| T-009 | Add Reactome fallback | Medium | If KEGG fails, Reactome is tried automatically |
| T-010 | Handle empty gene list and API errors | Medium | Returns empty pathway list with a status message |
| T-011 | Write `test_pathways.py` | High | Tests cover happy path, fallback, and empty input |

## Milestone 4: Vector Store

| **ID** | **Task** | **Priority** | **Done When** |
| --- | --- | --- | --- |
| T-012 | Implement `search_vectorstore(query, k)` | High | Returns top-k semantically similar abstracts from the store |
| T-013 | Implement abstract ingestion into the store | High | New PubMed abstracts are embedded and added automatically after retrieval |
| T-014 | Persist vector store to disk | Medium | Store survives app restarts |
| T-015 | Write `test_vectorstore.py` | High | Tests cover ingestion, search, and empty store |

## Milestone 5: LangGraph Agent

| **ID** | **Task** | **Priority** | **Done When** |
| --- | --- | --- | --- |
| T-016 | Define LangGraph state schema | High | State holds gene context, question, history, tool results, and final answer |
| T-017 | Wire pathway, PubMed, and vectorstore as agent tools | High | Agent can call all three tools during a turn |
| T-018 | Implement `run_agent(gene_context, question, history)` | High | Generator streams tokens; calls tools before synthesizing |
| T-019 | Add max-iteration guard | Medium | Agent stops after 5 tool calls and returns partial answer |
| T-020 | Write `test_agent.py` | High | Agent calls at least one tool and returns non-empty response |

## Milestone 6: Prompt Engineering

| **ID** | **Task** | **Priority** | **Done When** |
| --- | --- | --- | --- |
| T-021 | Write system prompt template for synthesis | High | Prompt instructs model to cite PMIDs, not hallucinate, and answer in plain language |
| T-022 | Write tool-result injection template | High | Pathway and abstract results are formatted clearly for the LLM |
| T-023 | Test prompts against demo dataset | Medium | Responses are biologically relevant for the CRC VisiumHD dataset |
| T-024 | Add fallback response when LLM unavailable | Medium | User sees a clear message instead of a crash |

## Milestone 7: Integration and Testing

| **ID** | **Task** | **Priority** | **Done When** |
| --- | --- | --- | --- |
| T-025 | Replace `run_model_inference` call in `worker.py` with `run_agent` | High | Existing chat flow uses the agentic pipeline end-to-end |
| T-026 | Verify streaming still works after integration | High | Tokens stream to the browser the same as before |
| T-027 | Run all unit tests | High | All tests in `docs/rules.md` section 8 pass |
| T-028 | End-to-end demo with CRC dataset | High | Draw ROI → get genes → agent calls tools → streamed answer with citations |
| T-029 | Record demo video | Medium | Demo shows ROI selection, tool calls, and streamed response |
| T-030 | Freeze final submission | High | Repo, docs, and demo are complete and ready for submission |
