# Functional Specifications: Spatial Omics Copilot

## 1. End-to-End Behavior

1. Researcher opens the app and uploads a whole-slide image (TIFF/OME-TIFF).
2. App converts the image to a pyramidal OME-TIFF and renders it in VivViewer.
3. Researcher optionally uploads an h5ad file; app processes and overlays spots/clusters.
4. Researcher draws an ROI polygon on the tissue.
5. App extracts the top differentially expressed genes within the ROI.
6. ROI gene context is passed to the agentic RAG pipeline.
7. LangGraph agent calls pathway enrichment tool, PubMed retrieval tool, and/or
   vector store search depending on the query.
8. Agent synthesizes a biological interpretation using the tool outputs.
9. Response streams token by token to the chat interface.
10. Researcher can ask follow-up questions; agent continues the conversation with
    full context of the selected region.

## 2. ROI Input Contract

| **Input** | **Format** | **Required Behavior** |
| --- | --- | --- |
| Whole-slide image | .tiff, .ome.tiff, .svs | Convert to pyramidal OME-TIFF; render via VivViewer proxy |
| Gene expression | .h5ad | Extract spot coordinates and expression matrix; overlay on image |
| ROI polygon | GeoJSON coordinates from VivViewer | Map to spatial coordinates; extract spots within bounds |

## 3. RAG Tool Contracts

### 3.1 Pathway Enrichment Tool

```python
def get_pathways(genes: list[str]) -> dict:
    """Query KEGG or Reactome for enriched pathways.

    Returns:
        {
            "pathways": [{"name": str, "id": str, "p_value": float}, ...],
            "source": "KEGG" | "Reactome"
        }
    """
```

Required behavior:
- Accept a list of gene symbols.
- Return top enriched pathways sorted by significance.
- Return an empty list (not an error) when no pathways are found.
- Use Reactome as fallback if KEGG is unavailable.

### 3.2 PubMed Retrieval Tool

```python
def search_pubmed(query: str, max_results: int = 5) -> list[dict]:
    """Search PubMed for relevant abstracts.

    Returns:
        [{"title": str, "abstract": str, "pmid": str, "year": int}, ...]
    """
```

Required behavior:
- Build the query from gene names and pathway names.
- Return up to `max_results` abstracts.
- Return an empty list (not an error) when no results are found.
- Respect PubMed API rate limits; use API key when configured.

### 3.3 Vector Store Search Tool

```python
def search_vectorstore(query: str, k: int = 3) -> list[dict]:
    """Semantic search over cached PubMed abstracts.

    Returns:
        [{"title": str, "abstract": str, "pmid": str, "score": float}, ...]
    """
```

Required behavior:
- Embed the query and retrieve the top-k most similar cached abstracts.
- Return an empty list when the store is empty.
- Update the store when new PubMed abstracts are fetched.

## 4. Agent Behavior

The LangGraph agent receives:
- The ROI gene context (gene names + expression values).
- The user's natural language question.
- The full chat history for the session.

The agent loop:
1. Decides which tools to call based on the question.
2. Calls tools and reads results.
3. Decides whether more tool calls are needed.
4. Synthesizes a final answer grounded in tool outputs.
5. Streams the final answer to the chat.

Required behavior:
- The agent must call at least one tool before answering a question about a region.
- The agent must not invent gene functions, pathway names, or paper citations.
- If all tools return empty results, the agent must say so rather than hallucinate.
- Tool call results must be visible in the chat as expandable context (optional for MVP).

## 5. Chat Interface Behavior

- Chat is scoped to one session per token.
- Each session stores messages as JSON in `chat_sessions/<session_id>/session.json`.
- Messages persist across page reloads.
- LLM responses stream token by token.
- Images (ROI thumbnails) are attached to the relevant assistant message.
- User can start a new session by clicking Reset.

## 6. Upload Behavior

- Whole-slide images are processed asynchronously; progress is shown via status bar.
- h5ad files are validated for required spatial coordinate fields before processing.
- Unsupported file types show a clear error and do not start processing.

## 7. Edge Cases

| **Edge Case** | **Expected Behavior** |
| --- | --- |
| ROI contains no spots | Return empty gene list; inform user |
| KEGG API unavailable | Fall back to Reactome; inform user if both fail |
| PubMed returns no results | Inform user; agent answers from gene context only |
| LLM unavailable | Show error; do not stream partial answers |
| h5ad missing spatial coordinates | Reject with clear error message |
| Image too large for memory | Use pyvips streaming; never load full image into RAM |
| Session file corrupted | Start a fresh session; log the error |
