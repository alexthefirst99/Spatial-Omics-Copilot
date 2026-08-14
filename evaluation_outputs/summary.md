# Spatial Omics Copilot Evaluation Summary

**Run status: COMPLETE — 10/10 ROI attempts persisted.**

Answers generated: 10/10; text judgments: 10/10; vision judgments: 10/10; ROI records with errors: 0; judge partial/error records: 0.

Metric denominators use only measurements that were actually available and valid. Unavailable measurements are reported as N/A; no missing result is fabricated.

## Technical Metrics

| Technical Metric | Result |
| --- | --- |
| PubMed retrieval relevance | 23/30 relevant; Precision@k=76.7% |
| Pathway relevance | 33/45 relevant (73.3%) |
| Image-to-gene connection | 9/10 PASS (90.0%) |
| Groundedness | 59/60 supported (98.3%) |
| Hallucination rate | claims 1/60 (1.7%); gene names 0/95 (0.0%) |
| Answer quality | biological reasonableness 4.10/5; ROI specificity 4.40/5; clarity 4.30/5; overall 4.27/5 |
| Response time | mean 23.55s; median 23.18s; min 16.96s; max 35.82s |

## Business Metrics

| Business Metric | Result |
| --- | --- |
| Time saved | 14376.45s (99.84%) mean saved per ROI |
| Workflow efficiency | 5 connected analysis/retrieval stages/ROI; 30 tool calls total (3.0/ROI); 0 manual data re-entry steps/ROI |
| Hypothesis usefulness | 4.10/5 mean |
| Decision quality | 4.10/5 mean |
| Trust and adoption | Trust 4.10/5; Adoption 4.10/5 |

## Audit notes

- Dataset: `/Users/quynhnguyen/Downloads/Spatial-Omics-Copilot/data/demo/Visium_HD_Human_Colon_Cancer_feature_slice.h5ad`
- H&E image: `/Users/quynhnguyen/Downloads/Spatial-Omics-Copilot/data/demo/Visium_HD_Human_Colon_Cancer_image.tif`
- Spatial coordinate frame: `spot_colrow_to_cytassist_colrow`
- Provider/model: `deepinfra:Qwen/Qwen3-VL-30B-A3B-Instruct`
- Validated agent traces: 10/10
- Persisted ROI attempts: 10/10. A report with fewer than 10 attempts is explicitly marked INCOMPLETE.
- Full ROI bounds, spot counts, crops, retrieved evidence, judgments, raw judge output, timings, errors, and traces are in `raw_results.json`.
