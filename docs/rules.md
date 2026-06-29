# Development Rules: Spatial Omics Copilot

## 1. General Coding Standards

- Keep functions small and single-purpose.
- Each `rag/` module owns exactly one responsibility (one tool per file).
- Keep RAG tool code separate from agent orchestration, session management, and UI.
- Do not hard-code local absolute paths; use environment variables or relative paths.
- Do not commit raw data files, model weights, credentials, or virtual environments.
- Use deterministic fallbacks when optional external APIs are unavailable.
- Never claim a result was retrieved unless the API call actually succeeded.

## 2. Security and Privacy Rules

- Store all API keys (OpenAI, Anthropic, PubMed) in environment variables only.
- Do not log API keys or user chat content to stdout in production.
- Do not upload user data or images to external services without explicit opt-in.
- Treat uploaded tissue images and gene expression files as sensitive research data.
- Summaries and interpretations are for research purposes only, not clinical diagnosis.

## 3. RAG and Agent Rules

- **Do not implement a new LLM client.** Use `run_model_inference` from `app/inference.py`
  as the base LLM call inside `rag/agent.py`. It already handles Ollama, OpenAI, and
  Claude switching, streaming, and API keys. The agent's job is to gather tool context
  first, build an enriched prompt, then call `run_model_inference(prompt, history)`.



- The agent must call at least one tool before answering a question about a tissue region.
- The agent must not invent gene functions, pathway names, or paper citations.
- Only cite PMIDs that were actually returned by the PubMed tool in that session.
- If all tools return empty results, the agent must say so explicitly.
- Limit the agent to a maximum of 5 tool calls per turn to prevent infinite loops.
- Pathway and PubMed results must be passed verbatim to the synthesis prompt;
  do not summarize or truncate them before the LLM sees them.

## 4. LLM Prompt Rules

- Prompts must include: ROI gene context, pathway results, paper abstracts, question.
- Do not include raw image data or file paths in prompts.
- Keep synthesis prompts under 8000 tokens to stay within context limits.
- Always instruct the model to cite sources by PMID when referencing literature.
- Provide a fallback response template when the LLM is unavailable.

## 5. API Usage Rules

- PubMed: use the E-utilities API; respect the 3 requests/second limit without a key,
  10 requests/second with a key. Cache results in the vector store to minimize calls.
- KEGG: use the KEGG REST API (free for academic use). Cache pathway results per gene list.
- Reactome: use the Reactome pathway analysis API as fallback for KEGG.
- Do not call external APIs synchronously on the main thread; use the worker queue.

## 6. Session and State Rules

- One session per token; sessions are stored in `chat_sessions/<session_id>/`.
- Use fcntl file locking for all session reads and writes.
- Write atomically using a `.tmp` file and `os.replace()`.
- Never modify `session_id` after creation.
- Clear the session only when the user explicitly clicks Reset.

## 7. Image Handling Rules

- Never load a full gigapixel image into RAM; always use pyvips streaming.
- Store OME-TIFF conversions in the working directory cache; do not regenerate
  if the cache file exists.
- ROI crops should be saved to a temp path and served via the `/preview` endpoint.
- Cache-bust image URLs with a timestamp query parameter when sending to the browser.

## 8. Testing Rules

Minimum required tests:

| **Test** | **Purpose** |
| --- | --- |
| `test_roi_extraction.py` | ROI polygon correctly selects spots and returns gene list |
| `test_pubmed.py` | PubMed tool returns expected schema; handles empty results |
| `test_pathways.py` | Pathway tool returns expected schema; falls back to Reactome |
| `test_vectorstore.py` | Abstracts are stored and retrieved correctly |
| `test_agent.py` | Agent calls tools and returns a non-empty response |
| `test_session.py` | Session read/write is correct under concurrent access |
| `test_upload.py` | h5ad upload validates spatial coordinates; rejects invalid files |
