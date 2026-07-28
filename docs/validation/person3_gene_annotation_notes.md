# Person 3 Gene Annotation Validation Notes

## Scope

This note validates T-049 and T-050. The module retrieves gene-level annotations from **NCBI Gene** using the official E-utilities sequence:

1. ESearch resolves an exact gene symbol in the selected organism.
2. ESummary retrieves the corresponding NCBI Gene document summary.

Each `GeneAnnotationResult` entry includes:

- queried symbol and returned official symbol
- full gene name
- aliases, when available
- NCBI functional summary, with the shorter description used only when no summary is available
- organism
- source database
- NCBI Gene ID and canonical NCBI Gene URL

## Trustworthiness and failure handling

- The default organism is `Homo sapiens` to avoid cross-species symbol ambiguity.
- Query symbols are validated before they are placed in an Entrez query.
- Missing and invalid symbols are reported in `missing_genes`; they are not silently converted into guessed annotations.
- API timeouts, HTTP errors, and malformed responses return a safe result with a status message.
- If ESearch succeeds but ESummary fails, the unresolved Gene IDs are not presented as complete annotations.
- Every returned functional statement keeps a direct NCBI Gene source ID or URL for citation by the agent.

## Biomedical relevance review for the colorectal cancer demo ROI

A real `GeneAnnotationResult` from the final colorectal cancer demo ROI was not present in the repository at implementation time. Specific genes are therefore not labeled as demo findings in this note.

For the final demo output, validate the top annotations with these checks:

1. Confirm that the returned official symbol matches the submitted ROI gene or a documented alias.
2. Confirm that the organism is `Homo sapiens`.
3. Use the NCBI functional summary as general gene-level background, not as evidence that the function is active in the selected ROI.
4. Do not infer colorectal-cancer relevance solely from a general gene annotation; combine it with DEG direction, pathway overlap, ROI image context, and PubMed evidence.
5. Flag summaries that are missing, overly broad, or primarily about another tissue context.
6. Preserve the NCBI Gene ID/URL in the final answer so users can verify the annotation.

## Final demo validation table

| Check | Status | Notes |
|---|---|---|
| NCBI Gene used as source | Implemented | ESearch followed by ESummary |
| Official symbol and full name returned | Implemented | When available in the NCBI DocSum |
| Aliases returned | Implemented | Parsed from `otheraliases` |
| Functional summary returned | Implemented | NCBI `summary`, description fallback |
| Organism and provenance retained | Implemented | Includes NCBI Gene ID and URL |
| Top colorectal demo genes reviewed | Pending demo output | Update after Person 6 runs the final ROI |

## References

- NCBI Gene: https://www.ncbi.nlm.nih.gov/gene/
- NCBI E-utilities overview: https://www.ncbi.nlm.nih.gov/books/NBK25497/
- NCBI E-utilities usage guidance: https://www.ncbi.nlm.nih.gov/books/NBK25499/
