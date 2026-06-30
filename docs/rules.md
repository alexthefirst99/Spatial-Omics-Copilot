# Development Rules: Spatial Omics Copilot

## 1. General Coding Standards

- Keep functions small and single-purpose.
- Each `rag/` submodule owns exactly one responsibility.
- Keep RAG analysis code separate from app infrastructure (routes, session, worker).
- Do not hard-code local absolute paths; use environment variables or relative paths.
- Do not commit raw data files, model weights, credentials, or virtual environments.
- Use deterministic fallbacks when optional external APIs are unavailable.
- Never claim a result was retrieved unless the API call actually succeeded.

## 2. Security and Privacy Rules

- Store all API keys (OpenAI, Anthropic, PubMed) in `.env` only — never in code.
- Do not log API keys or user chat content to stdout in production.
- Do not upload user data or images to external services without explicit opt-in.
- Treat uploaded tissue images and gene expression files as sensitive research data.
- Interpretations are for research purposes only, not clinical diagnosis.

## 3. Architecture Rules

- **`rag/` is pure analysis** — no HTTP handling, no streaming, no session writes.
- **`app/` owns the LLM call** — `worker.py` calls `inference.py` directly. No code inside `rag/` should call the LLM or import from `app/`.
- **One entry point** — `routes.py` and `app.py` only import `run_agent` from `rag.agent`. Never import individual submodules (`rag.deg`, `rag.pathway`, etc.) from outside `rag/`.
- **`run_agent()` owns the output contract** — whatever is inside `rag/agent/graph.py` is JN's business. The output dict format must not change.

## 4. RAG Module Rules

- Each submodule (`deg/`, `pathway/`, `pubmed/`, `agent/`) exposes its public function only through its `__init__.py`.
- Output formats are fixed — see `docs/specs.md` section 3 for each module's contract.
- Adding extra fields to output dicts is allowed; removing or renaming existing fields is not.
- Return empty lists or `None` on failure — never raise unhandled exceptions from a tool.
- The agent must call at least one tool before answering a question about a tissue region.
- The agent must not invent gene functions, pathway names, or paper citations.
- Only cite PMIDs that were actually returned by the PubMed tool in that turn.
- Limit the agent to a maximum of 5 tool calls per turn to prevent infinite loops.

## 5. API Usage Rules

- PubMed: use NCBI E-utilities; respect 3 req/s without key, 10 req/s with `PUBMED_API_KEY`.
- Pathway: use gseapy (local) or g:Profiler REST API — no API key required.
- Do not call external APIs on the main thread; processing happens in `worker.py`.

## 6. Session and State Rules

- One session per token; stored in `chat_sessions/<session_id>/`.
- Use fcntl file locking for all session reads and writes.
- Write atomically using a `.tmp` file and `os.replace()`.
- Clear the session only when the user explicitly clicks Reset.

## 7. Image Handling Rules

- Never load a full gigapixel image into RAM; always use pyvips streaming.
- Store OME-TIFF conversions in the working directory cache; do not regenerate if cache exists.
- ROI crops saved to temp path and served via the `/preview` endpoint.

## 8. Testing Rules

| **Test** | **Purpose** |
| --- | --- |
| `test_deg.py` | ROI / cluster correctly selects spots and returns gene list |
| `test_pubmed.py` | PubMed tool returns expected schema; handles empty results |
| `test_pathway.py` | Pathway tool returns expected schema; handles empty gene list |
| `test_agent.py` | Agent calls tools and returns complete output dict |
| `test_session.py` | Session read/write correct under concurrent access |
| `test_upload.py` | h5ad upload validates spatial coordinates; rejects invalid files |
