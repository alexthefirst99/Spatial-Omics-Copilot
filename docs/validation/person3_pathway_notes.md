# Person 3 Pathway Enrichment Validation Notes

## Scope

This note validates the implementation for T-012 to T-014. The module submits the top ROI gene symbols to Enrichr through `gseapy.enrichr` using these libraries by default:

- `GO_Biological_Process_2023`
- `KEGG_2021_Human`

The returned `PathwayResult` records the pathway name, source library, adjusted p-value, overlapping ROI genes, overlap size, gene-set size, nominal p-value, odds ratio, and combined score when Enrichr supplies those fields.

## Safety and statistical checks

- Empty gene lists return an empty result and do not call Enrichr.
- Duplicate gene symbols are removed before submission.
- API, dependency, and malformed-response errors return an empty result with a status message instead of crashing the app.
- Results are sorted by adjusted p-value.
- By default, only pathways with adjusted p-value `<= 0.05` are returned.
- No synthetic pathways, random p-values, or tissue-inappropriate fallback genes are generated.
- The source library is retained so the copilot can distinguish a GO biological process from a KEGG pathway.

## Biomedical relevance review for the colorectal cancer demo ROI

A real `PathwayResult` from the final colorectal cancer demo ROI was not present in the repository when this module was implemented. Therefore, no specific pathway is claimed to be a true result here.

When Person 6 runs the final demo, review the top pathways using the following acceptance criteria:

1. Confirm that each interpreted pathway passes the adjusted p-value threshold and is supported by multiple overlapping ROI genes when possible.
2. Prefer specific, coherent terms over very broad terms such as generic metabolism, binding, or transcription.
3. Check whether the overlap genes form a biologically consistent colorectal-cancer or tumor-microenvironment signal rather than relying on the pathway title alone.
4. Treat one-gene overlaps and highly redundant GO terms cautiously.
5. Report enrichment as association, not proof that the pathway is activated in the tissue.
6. Record the exact Enrichr library names and returned overlap genes in the final validation update.

## Final demo validation table

| Check | Status | Notes |
|---|---|---|
| Real Enrichr call used | Implemented | `gseapy.enrichr`, no mock pathway database |
| GO BP 2023 queried | Implemented | Default source library |
| KEGG 2021 Human queried | Implemented | Default source library |
| Adjusted p-values retained | Implemented | Default cutoff is 0.05 |
| Overlap genes retained | Implemented | Used for biological interpretation |
| Colorectal demo pathways reviewed | Pending demo output | Update this row after the final real ROI run |

## References

- GSEApy Enrichr documentation: https://gseapy.readthedocs.io/en/latest/gseapy_example.html
- Enrichr: https://maayanlab.cloud/Enrichr/
