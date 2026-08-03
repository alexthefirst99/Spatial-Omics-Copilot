# Product Requirements Document: Spatial Omics Copilot

| **Team** | **Project Type** | **Date** |
| --- | --- | --- |
| Group | Class prototype | June 2026 |

## 1. Product Summary

The Spatial Omics Copilot is an AI-powered research tool that lets scientists
select any tissue region in a gigapixel histopathology image and immediately
receive biologically grounded, literature-backed interpretations. It combines
spatial transcriptomics visualization, pathway enrichment, PubMed retrieval,
and an agentic LLM pipeline in a single interface.

## 2. Goals

| **ID** | **Goal** | **Success Target** |
| --- | --- | --- |
| G1 | Demonstrate spatial omics visualization | Researcher can load a whole-slide image and h5ad file and view gene expression overlays |
| G2 | Enable region-level gene analysis | Drawing an ROI returns the top differentially expressed genes for that region |
| G3 | Provide pathway context | Top genes map to enriched biological pathways via GO and KEGG |
| G4 | Ground responses in literature | Relevant PubMed abstracts are retrieved and summarized when literature evidence is needed |
| G5 | Deliver agentic reasoning | LangGraph agent decides which tools to call and synthesizes a coherent interpretation |
| G6 | Support conversational follow-up | Researcher can ask follow-up questions about the selected region in the chat interface |
| G7 | Stream responses in real time | LLM responses appear token by token without full-page refresh |

## 3. Product Scope

### In Scope

- Whole-slide image upload and OME-TIFF pyramid rendering via VivViewer.
- h5ad spatial gene expression file upload and spot/cell overlay.
- ROI drawing on the tissue and top-gene extraction from the selected region.
- ORA pathway enrichment against GO and KEGG via gseapy.
- PubMed NCBI E-utilities retrieval based on genes and pathways when literature evidence is needed.
- LangGraph agentic loop that dynamically decides which tools to call.
- ChromaDB semantic-search module for fetched PubMed abstracts, with PMID metadata preserved; wired into the agent via `copilot_agent.semantic_rerank` (off by default).
- Streaming chat interface with AGENT TRACE, pathway, and DEG panels.
- Session persistence so chat history survives page reloads.

### Out of Scope

- Production clinical or diagnostic use.
- Multi-user concurrency guarantees beyond a classroom demo.
- Uploading raw data to external cloud storage.
- Legal or clinical conclusions.
- Real-time collaboration between multiple researchers.

## 4. Success Metrics

| **Metric** | **Target** |
| --- | --- |
| Image rendering | Gigapixel OME-TIFF loads and tiles correctly in the viewer |
| ROI gene extraction | Top N genes returned for any drawn region within 5 seconds on a coarsely-binned or downsampled dataset (~17k spots or fewer); the reference group is always the full remaining dataset regardless of ROI size, so a full-resolution dataset costs much more — on the shipped ~137k-spot demo dataset this is closer to ~2 minutes, not 5 seconds. See `docs/tickets.md` T-010 and `docs/validation/person2_deg_notes.md`. |
| Pathway enrichment | GO / KEGG returns at least one enriched pathway for a valid gene list |
| PubMed retrieval | Up to 3 relevant abstracts returned when literature evidence is requested; no unrelated padding |
| Agent tool use | Agent calls the appropriate tools based on the user question, and the trace reflects the tools actually used |
| Response streaming | First token appears within 3 seconds of submitting a query |
| Chat history | Messages persist across page reloads within the same session |
