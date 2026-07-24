# Person 4 PubMed Validation Notes

## Scope

Validated on July 23, 2026 with the live NCBI PubMed E-utilities backend.
The final colorectal-cancer demo `.h5ad`, ROI, `DEGResult`, and
`PathwayResult` are not checked into this repository yet. Therefore this is a
pre-integration validation using a representative colorectal-cancer input:

- Genes: `EPCAM`, `KRT20`, `CEACAM5`, `SPP1`, `COL1A1`
- Pathways: Wnt signaling, epithelial-mesenchymal transition, extracellular
  matrix organization
- Disease context: colorectal cancer

The table must be refreshed with the actual ROI genes and pathways during the
Person 6 end-to-end demo/evaluation task. These notes must not be presented as
clinical interpretation.

## Live retrieval review

The live query returned five PubMed records without unrelated padding.

| PMID | Short relevance assessment | Rating |
| --- | --- | --- |
| [41051794](https://pubmed.ncbi.nlm.nih.gov/41051794/) | Directly connects SPP1 with colorectal-cancer liver metastasis, cancer-associated fibroblasts, immune resistance, and an epithelial-mesenchymal program. Strong mechanistic context for an SPP1/EMT-like ROI, although the abstract alone does not prove that the same mechanism occurs in the selected ROI. | High |
| [35303421](https://pubmed.ncbi.nlm.nih.gov/35303421/) | Studies colorectal cancer and matched liver metastasis at single-cell level and discusses SPP1-associated myeloid states. Useful immune-microenvironment evidence, but less directly tied to the other candidate genes and pathway labels. | Medium |
| [35365629](https://pubmed.ncbi.nlm.nih.gov/35365629/) | Single-cell and spatial study of colorectal cancer with FAP-positive fibroblasts and SPP1-positive macrophages. This is the strongest match for spatial tissue context and SPP1-driven stromal/immune interaction. | High |
| [34798986](https://pubmed.ncbi.nlm.nih.gov/34798986/) | Relevant to colorectal-cancer risk through EPCAM deletions in Lynch syndrome, but EPCAM is used as a hereditary alteration rather than ROI gene-expression evidence. It should be demoted for questions about spatial expression. | Low for ROI interpretation |
| [37691929](https://pubmed.ncbi.nlm.nih.gov/37691929/) | Connects SPP1-positive macrophages and cancer-associated fibroblasts with colorectal-cancer metastasis. Relevant biological support for an SPP1/stromal ROI, though it is not itself a spatial-transcriptomics validation of this sample. | Medium-high |

## Conclusions

- Four of five results were useful for the representative SPP1/stromal/immune
  context and all five matched the colorectal-cancer constraint.
- Exact gene matches can still be biologically misleading. PMID 34798986
  matches `EPCAM`, but its hereditary-deletion context differs from high EPCAM
  expression in an ROI. PMID presence alone is not sufficient evidence.
- The semantic-search API should be wired into the agent with the user's
  question to prioritize papers about the requested mechanism and demote
  incidental gene-symbol matches.
- The agent should cite only PMIDs returned in the current turn, distinguish
  association from causation, and state that literature evidence supports an
  interpretation rather than proving the identity or clinical status of an
  ROI.
- Final acceptance still requires rerunning this review on the actual demo
  `PubMedResult` once Person 2/3/6 provide the final ROI genes, pathways, and
  end-to-end output.
