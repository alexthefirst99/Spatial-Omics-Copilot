# Evaluation Benchmark Audit

This audit evaluates the production scientific-assistant workflow, not biological ground truth or clinical validity. The revised benchmark retains 28 queries across 10 inline synthetic fixtures.

## Case audit

| Case ID | Category | Decision | Problem found | Change |
|---|---|---|---|---|
| `gene_epcam_function` | Gene lookup | REWRITE | “In this ROI” could invite an expression-specific claim from a general annotation. | Asked for EPCAM's known function. |
| `pathway_epithelial_programs` | Pathway enrichment | REWRITE | The question disclosed the intended epithelial interpretation. | Removed the answer-bearing label and asked for enriched processes. |
| `literature_epcam_tumor_epithelium` | Literature retrieval | REWRITE | Naming EPCAM also triggers gene annotation, and PubMed searches all ROI genes rather than only EPCAM. | Asked for ROI-relevant colorectal literature; PubMed is the sole required tool. |
| `multi_epithelial_identity` | Multi-step interpretation | REWRITE | “Represent” was too definitive and the question unnaturally named all tools. | Asked which state is suggested, with support and limitations; broad interpretation invokes all three tools. |
| `gene_hladra_immune_process` | Gene lookup | REWRITE | “Biological processes” forced the pathway route, contrary to the expected gene route. | Asked what HLA-DRA is and encodes. |
| `pathway_immune_strongest_signal` | Pathway enrichment | REWRITE | “Strongest signal” could be read as pathway activity. | Asked which immune-associated pathways are enriched. |
| `literature_hladra_activation` | Literature retrieval | REWRITE | Naming HLA-DRA scheduled gene annotation as well as PubMed; retrieval uses the complete ROI list. | Asked for literature relevant to immune-associated colorectal regions. |
| `multi_immune_state` | Multi-step interpretation | REWRITE | “Determine” overstated what five synthetic markers establish and explicitly prescribed tools. | Asked what state is suggested and requested uncertainty. |
| `multi_immune_cell_mixture` | Multi-step interpretation | REWRITE | The original wording was mostly sound but over-prescribed evidence sources. | Retained the T-cell/myeloid comparison and made the uncertainty request natural. |
| `gene_mki67_top2a_functions` | Gene lookup | KEEP | Answerable from NCBI Gene and correctly routed. | No query or expectation change. |
| `pathway_proliferation_programs` | Pathway enrichment | REWRITE | “Gene list” also scheduled gene annotation, contradicting the one-tool expectation. | Asked directly for enriched cell-cycle pathways. |
| `literature_mki67_regions` | Literature retrieval | REWRITE | A MKI67-specific ask was not guaranteed by a PubMed query built from every ROI gene. | Asked for literature relevant to proliferative colorectal regions. |
| `multi_proliferative_state` | Multi-step interpretation | REWRITE | “Active proliferation” was stronger than an unranked enrichment interpretation alone supports. | Asked how strongly the ROI supports a proliferation-associated interpretation. |
| `gene_col1a1_stromal_role` | Gene lookup | REWRITE | Mixed a database lookup with an ROI-state judgment. | Restricted the question to COL1A1 and its protein function. |
| `pathway_stromal_ecm` | Pathway enrichment | REWRITE | The fixture's intended answer was exposed in the question. | Asked which enrichment results support ECM or stromal interpretation. |
| `literature_col1a1_stroma` | Literature retrieval | REWRITE | Naming COL1A1 scheduled annotation too, while PubMed searches all ROI genes. | Asked for colorectal tumor-stroma literature relevant to the ROI. |
| `multi_stromal_state` | Multi-step interpretation | REWRITE | “Characterize” and “fibroblast-rich” were too categorical for five genes. | Reframed as alternatives and required uncertainty. |
| `gene_krt19_epithelial_marker` | Gene lookup | REWRITE | Combined gene lookup with a preselected cell-identity conclusion. | Asked only what KRT19 encodes and its known function. |
| `pathway_mixed_compare_programs` | Pathway enrichment | REWRITE | “Mixed ROI” disclosed the intended interpretation. | Asked whether both epithelial- and immune-associated pathways are enriched. |
| `literature_epcam_hladra_combination` | Literature retrieval | REWRITE | Requested co-occurrence evidence, but production PubMed combines ROI genes with OR and cannot require EPCAM/HLA-DRA co-occurrence. | Asked for literature relevant to regions with both broad signals. |
| `multi_mixed_boundary` | Multi-step interpretation | REMOVE | A single region without neighboring regions or geometry cannot establish a tumor-immune interface. | Replaced with a within-ROI admixture question that states what cannot be concluded. |
| `multi_mixed_alternatives` | Multi-step interpretation | REMOVE | The supplied data cannot distinguish infiltration from antigen-presenting tumor cells. | Replaced with plausible epithelial/T-cell/myeloid mixture alternatives and explicit limitations. |
| `multi_epithelial_proliferative` | Multi-step interpretation | REWRITE | The fixture label leaked the target answer and the query prescribed every tool. | Used a neutral label and asked whether the state is suggested and how confident to be. |
| `negative_fake_gene_lookup` | Negative/failure | KEEP | Valid invalid-symbol/no-record test with a safe NCBI empty-result path. | No query or expectation change. |
| `negative_fake_gene_literature` | Negative/failure | REWRITE | Naming the fake symbol scheduled gene annotation in addition to PubMed. | Kept the no-literature test but made PubMed the unambiguous sole route. |
| `negative_weak_enrichment` | Negative/failure | REWRITE | “Gene” scheduled annotation, and a one-gene ORA can return a result even though interpretation remains weak. | Asked for any returned pathways and required caution rather than assuming an empty result. |
| `negative_no_roi_evidence` | Negative/failure | REWRITE | The word “evidence” forced the literature route despite the expected biological-explanation route. | Asked what can be inferred from the supplied data; no tools run without genes. |
| `negative_ambiguous_state` | Negative/failure | REWRITE | “Evidence” forced PubMed only, contradicting the expected broad three-tool workflow. | Rephrased as a broad state question so all evidence tools run and insufficiency must be stated. |

## Totals

- Kept unchanged: 2
- Rewritten: 24
- Removed and replaced in the same category slots: 2
- Final category counts: 5 gene lookup, 5 pathway enrichment, 5 literature retrieval, 8 multi-step interpretation, 5 negative/failure handling
- Original route/tool expectation mismatches: 10 of 28
- Scientifically unsupported or unavailable-input questions: 2, both replaced (`multi_mixed_boundary`, `multi_mixed_alternatives`)

## Production contract found

Each inline evaluation turn supplies the question, ROI label and selection type, disease context when present, and ordered gene objects containing a symbol and synthetic `log2_fold_change`. The prompt describes these as top differentially expressed genes relative to the rest of the tissue. No fixture supplies an ROI image, morphology, spot/cell counts, segmentation, contacts, multiple regions, or spatial coordinates. No adjusted p-values are supplied.

The deterministic production router emits `gene_function`, `pathway`, `literature`, `biological_explanation`, `image_pattern`, `roi_summary`, or `general_chat`. Its evidence tools are exactly `gene_annotation_tool`, `pathway_tool`, and `pubmed_tool`.

Multi-tool behavior is genuinely implemented. A broad biological interpretation with genes schedules NCBI Gene annotation, Enrichr over-representation analysis, then PubMed retrieval. Explicit questions schedule only the evidence types detected in the question. The graph executes one tool per step with a five-call limit; pathway names can refine PubMed retrieval later in the same turn.

NCBI Gene returns verified human gene summaries and missing-symbol status. Enrichr returns significant GO Biological Process 2023 and KEGG 2021 Human terms, adjusted p-values, and overlap genes; this is over-representation, not proof of pathway activity. PubMed returns up to three current ESearch/EFetch records with PMID, title, abstract, journal, and year. Its query uses all ROI genes, available pathway names, and the disease anchor; the user's wording is not part of the PubMed query unless optional semantic reranking is enabled. Exact papers and ordering are therefore intentionally not benchmark ground truth.

The final LLM receives the question plus the observable evidence context through the same inference boundary as production. Automatic expectations remain limited to route, required tool calls, retrieval/run status, and grounding artifacts; biological correctness and uncertainty quality remain human-review judgments.

## Fixture audit

All non-negative symbols are valid human gene symbols, no fixture contains duplicates, and the positive combinations plausibly represent broad epithelial-associated, immune-associated, proliferation-associated, stromal/ECM-associated, mixed epithelial/immune, or epithelial/proliferation signals. `FAKEGENE12345` is intentional; `RPLP0` is an intentional weakly discriminative housekeeping-gene control.

Descriptive fixture labels were passed into the model's evidence block and leaked the intended interpretation. All ten labels are now neutral (`Synthetic ROI 01` through `Synthetic ROI 10`). Internal ROI IDs remain descriptive for auditability, but the prompt formatter does not expose `roi_id` to the LLM.

## Limitations revealed

- Literature retrieval cannot target only a named gene when several ROI genes are present; it queries all ROI genes as alternatives.
- A single synthetic ROI cannot establish cell counts, proportions, cell-cell interaction, an interface, infiltration, spatial gradients, morphology, diagnosis, causality, or clinical significance.
- Enrichment from five genes is a small-set ORA and should be interpreted as represented/enriched programs, not measured pathway activity.
- The live-service benchmark distinguishes empty results from tool failures in its trace, but external results can change. It does not require exact PMIDs or term ordering.
- The benchmark does not deterministically inject a network outage; service failures are measured when observed, while invalid input, empty retrieval, missing input, and insufficient information have explicit negative cases.

## Validation performed

- `evaluation/eval_cases.json` parses as valid JSON and passes `load_cases` validation.
- The routing, agent, NCBI Gene, Enrichr, PubMed, and evaluation suites completed with 127 passed and 1 skipped.
- `python3 -m evaluation.runner --help` completed successfully.
- The 28-case no-generation run produced 28 records with no route mismatches, no expected-tool mismatches, no unexpected tools, and no run-level errors. NCBI Gene and PubMed returned usable evidence for all positive calls; the two fake-gene retrievals correctly returned empty results.
- All 15 pathway calls correctly reported a tool error in this host environment because the repository-pinned GSEApy 1.1.2 has no Python 3.14 wheel and Rust is unavailable. They were not misclassified as biological absence. The generated summary now lists these failures instead of printing a contradictory “None.”
- One live DeepInfra smoke generation (`gene_epcam_function`) completed with the expected route and tool, a nonempty answer, no error, and an automatic grounding score of 1.0. The remaining 27 hosted generations were not run.
