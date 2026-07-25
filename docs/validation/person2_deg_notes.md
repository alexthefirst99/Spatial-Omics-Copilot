# Person 2 — DEG Extraction Validation Notes

Owner: Rodney (Person 2) · Tickets: T-008, T-009, T-010, T-011, T-044
Module: `src/rag/deg/`

---

## 1. What the statistics do and do not establish

### What the test is

For each candidate gene, a **two-sided Wilcoxon rank-sum test** (equivalently
Mann-Whitney U) compares the distribution of that gene's stored values inside
the selection against the distribution outside it. The null hypothesis is that
a randomly drawn selected spot is equally likely to rank above or below a
randomly drawn reference spot.

### What it does NOT establish

These caveats are not boilerplate. Each one can independently invalidate a
conclusion drawn from this output.

1. **Not biological significance.** A small p-value means the two rank
   distributions differ more than sampling noise explains. It says nothing
   about effect magnitude, cell type identity, or mechanism.

2. **Spots are not independent replicates.** Neighbouring spots in spatial
   transcriptomics are spatially autocorrelated. The effective sample size is
   substantially smaller than the spot count, so p-values are
   **anti-conservative** — they are smaller than they should be.

3. **The selection is chosen by looking at the data.** A drawn ROI is chosen by
   eye from the rendered expression overlay; a cluster is derived from the very
   same expression matrix by `run_spatial_clustering`. Testing the matrix that
   defined the groups is **circular**. For the cluster path in particular, the
   p-values are descriptive rankings, not valid inferential statements. This is
   the classic "double dipping" problem and it is not fixed by FDR correction.

4. **No library-size correction.** See section 3 — this is the largest caveat.

5. **A gene absent from the reference is not "infinitely enriched".** The
   log2 fold change uses a `1e-9` pseudocount, so a gene detected in the ROI
   and nowhere else produces a very large but essentially arbitrary value
   driven by the pseudocount, not by biology.

**Practical guidance for downstream consumers (Person 5 / Person 6):** present
these as *candidate marker genes ranked by fold change*, never as "significantly
differentially expressed genes" without the qualifiers above. The
`fdr_applied` boolean in the result exists precisely so the UI and the LLM
prompt can tell the difference.

---

## 2. Tie and continuity conventions

| Convention | Choice | Why |
| --- | --- | --- |
| Tie correction | **Applied** | Spatial expression is zero-inflated, so ties dominate. The uncorrected variance `n1*n2*(N+1)/12` is too large and yields conservative, wrong p-values. The standard correction subtracts `sum(t^3 - t) / (N*(N-1))` from the `(N+1)` term. |
| Continuity correction | **Applied**, classical form | 0.5 is subtracted from `abs(U - mu)` before dividing by sigma. This is `scipy.stats.mannwhitneyu(use_continuity=True)`, its default. |
| Null distribution | **Normal approximation always** (`method="asymptotic"`) | With ties present the exact permutation null is invalid anyway, and these datasets are far above the size where the approximation matters. |
| Alternative | **Two-sided** | The ranking then filters to `log2fc > 0`, so direction is handled by the effect size rather than by a one-sided test. |

### Why tie correction is not cosmetic

Measured on the zero-inflated fixture in `test_deg.py` (100 spots, ~90% zeros):

```
tie-corrected sigma   112.82  ->  p = 1.11e-06
uncorrected sigma     145.06  ->  p = 1.52e-04
                                  ratio: 136x
```

The test asserts the module's p-value against the closed-form tie-corrected
value, **not** merely that "p is small" — on this input the uncorrected p is
also below 0.001, so a loose threshold would have silently passed even with tie
correction switched off.

---

## 3. NORMALIZATION — the largest caveat

**The module runs on `adata.X` exactly as stored in the uploaded `.h5ad`. It
does not normalize.**

Why: `preprocess_adata` (normalize_total + log1p) is only ever called *inside*
`run_spatial_clustering`, and it never writes its result back to disk. The DEG
path reads `state["h5ad_path"]`, which points at the raw uploaded
`spatial_expression.h5ad`. **There is no normalized matrix on disk for this
module to read.**

### The consequence

A Wilcoxon test on library-size-uncorrected values is **confounded by per-spot
sequencing depth**. A spot with a higher total count tends to rank higher for
essentially every gene. An ROI that happens to contain deeper-sequenced spots
will therefore show apparent enrichment across the board — a technical gradient
can manufacture a whole-transcriptome "signal".

This is not a footnote. It is the dominant threat to validity of this module's
output today.

### What is reported instead of assumed

`expression_source` records what was **observed**, never what is assumed:

| Value | Meaning |
| --- | --- |
| `raw_counts_unnormalized` | Every stored value is a finite integer. |
| `non_integer_values_provenance_unknown` | At least one value is fractional or non-finite — the matrix may already be normalized, log-transformed, or something else. The module does not guess. |
| `no_values_observed` | The matrix has no stored values. |

### Opt-in normalization

`compute_deg(..., normalize=True)` applies library-size normalization to 1e4
per spot followed by `log1p`. It is **OFF by default** deliberately: enabling it
changes the `log2_fold_change` numbers already rendered in the UI. Both the
effect size and the test statistic are always computed from the *same* matrix,
so raw and normalized values are never mixed.

---

## 4. BH denominator definition

`n_genes_tested` **is** the denominator, and it is emitted in the result so the
correction is auditable from the output alone.

```
n_genes_input          genes present in the uploaded matrix
  - n_genes_filtered_out   removed by the min_cells detection pre-filter (T-010)
  = candidates
  - n_genes_untestable     kept, but not meaningfully testable
  = n_genes_tested         <-- the BH denominator, m
```

A gene is **untestable** (excluded from `m`, assigned `adj_pvalue = 1.0`, and
tagged with `untestable_reason`) when:

| Reason code | Condition |
| --- | --- |
| `insufficient_spots` | Fewer than 3 spots on either side. Applies to the whole run — empty ROI, single-spot ROI, or an ROI covering every spot. |
| `constant_expression` | The gene takes one value across all spots. Its tie-corrected variance is exactly 0, so a naive z would divide by zero and emit NaN. |
| `no_genes` | The matrix has no columns. |

**Why exclude rather than count them (refinement R1):** a constant gene is not
a test that happened to be non-significant — it is a test that could not be
performed. Counting it inflates `m` and makes every genuine correction
needlessly conservative. On a typical VisiumHD section a large fraction of
genes are all-zero within a small ROI, so this materially changes the
adjusted p-values.

BH itself is implemented directly (no statsmodels): sort ascending, scale by
`m / rank`, enforce monotonicity with a running minimum taken from the largest
p-value downwards, then clamp to `[0, 1]`. Verified in `test_deg.py` against a
hand-computed vector where monotonicity actually binds
(`0.065 -> 0.042`).

**Guarantee:** no code path can emit NaN, infinity, or a value outside `[0, 1]`
into `pvalue` or `adj_pvalue`. Enforced at three layers (constant-gene guard,
non-finite coercion in `wilcoxon_rank_sum`, clamping in `benjamini_hochberg`)
and asserted across the suite by `_assert_pvalues_are_sane`.

---

## 5. Performance status — T-010 MET

Measured on this machine (Python 3.11.9, Windows, synthetic sparse data at 5%
density, full `run_roi_deg` path including h5ad read, polygon validation, mask
build, pre-filter, test, and BH).

### T-010 result — MET

**T-010 ("under 10 s on a 3,000-spot dataset") is the only binding performance
requirement.** Both expression profiles clear it:

| 3,000 spots x 18,000 genes | Before | After | Verdict |
| --- | --- | --- | --- |
| Uniform 5% density | 25.6 s | **3.06 s** | ✅ 8.4x |
| Long-tailed (realistic) | — | **1.37 s** | ✅ |

**Both profiles matter, and the difference is the whole point of T-010.**

| Profile | nnz | median detections/gene | genes < 10 detections | filtered | BH denominator |
| --- | --- | --- | --- | --- | --- |
| Uniform 5% | 2,700,000 | 150 | 0 (0%) | 0 | 18,000 |
| Long-tailed | 860,206 | 7 | 9,951 (55%) | 9,951 | **8,049** |

The uniform benchmark is the pre-filter's **worst case**: every gene has ~150
detections, so `min_cells=10` removes nothing. Under a realistic log-normal
detection profile the pre-filter removes **55% of genes**, more than halving the
BH denominator — which makes every surviving correction less conservative, not
just faster. The realistic figure is what T-010 is actually about.

### What changed

Profiling showed a single stage was ~100% of runtime:

```
prefilter        0.01 s
expression_source 0.01 s
means / pct      0.06 s
wilcoxon        37.77 s   <-- ~100% of runtime
BH               0.00 s
```

`scipy.stats.mannwhitneyu(..., axis=0)` does not stay vectorised through its
`_axis_nan_policy` wrapper. It was replaced with a hand-rolled vectorised
rank-sum (`_rank_sum_block` in `stats.py`) that computes midranks once with
`scipy.stats.rankdata(..., axis=0)` and derives the tie-corrected variance from
those same ranks via

```
Var(U) = n1*n2 / (N*(N-1)) * (sum(R^2) - N*((N+1)/2)^2)
```

so ranking is the only sort and it serves both the statistic and the variance.
A prototype that counted tie groups separately measured only ~3x; reusing the
ranks removed the remaining per-column Python loop and delivered **8.4x**.

**This reimplementation is gated by permanent tests.**
`test_hand_rolled_rank_sum_matches_scipy` runs both implementations over six
fixed synthetic matrices — heavy ties, binary (extreme ties), unequal group
sizes, no ties at all, mixed degenerate + signal, and minimum group size — and
requires agreement with `scipy.stats.mannwhitneyu(method="asymptotic",
use_continuity=True)` to `rtol=1e-9` on both p-value and statistic. Chunk-size
invariance and the R5 no-NaN/inf/out-of-range guarantee are asserted alongside.
**Do not delete those tests** — they are what makes not calling scipy
defensible.

Explicitly **not** done, as out of scope: float32 ranks, dropping the CSC copy,
and parallelism.

### Memory — and a measurement error worth recording

The DEG path uses **~1.29 GB** on 30,000 x 18,000 (peak RSS 1,405 MB from a
114 MB baseline), far under the 4 GB ceiling.

The first measurement of this was wrong in an instructive way. Generating the
data and running the analysis in one process reported a ~3.0 GB peak — but the
peak *equalled* the baseline captured after data construction. Since
`PeakWorkingSetSize` is a high-water mark that never decreases, that equality
proves the peak belonged to building the 27M-nonzero synthetic matrix and
writing the h5ad, and that `run_roi_deg` never rose above it. **A peak equal to
a post-construction baseline says nothing about the code under test.**
Re-measuring in a clean process that only reads a pre-built file gave the real
figure. (That clean run predates the rank-sum swap; the new path allocates a
comparable rank block per chunk.)

The sparse-only design holds: no stage densifies a full matrix, only
`n_spots x chunk_size` gene blocks.

---

## 5a. Dataset size and VisiumHD bin selection — DEMO CONFIGURATION ITEM

T-010 is met at 3,000 spots, but the **demo dataset named in `README.md` is
VisiumHD Human Colorectal Cancer**, and VisiumHD at fine bin sizes is one to
three orders of magnitude larger than that. This section documents the limit
rather than engineering around it.

### Measured scaling

Realistic long-tailed profile, 18,000 genes, full `run_roi_deg` path,
ROI covering half the spots:

| Spots | nnz | Wall time | BH denominator |
| --- | --- | --- | --- |
| 3,000 | 794,172 | **1.44 s** | 8,106 |
| 10,000 | 2,697,505 | **5.45 s** | 12,252 |
| 30,000 | 8,342,916 | **18.40 s** | 15,172 |
| 60,000 | 16,824,916 | **45.42 s** | 16,436 |

Scaling is mildly superlinear in spot count, roughly **O(n^1.11)** — the
rank-sum sorts each gene column (`n log n`), and larger sections also push more
genes past the `min_cells` pre-filter, so the BH denominator grows too.

### The 10-second threshold

Fitting the 10k -> 30k points gives `t = 5.45 * (n/10000)^1.107`, so
`run_roi_deg` stays under 10 s up to roughly:

> **~17,000 spots**

### What that means for VisiumHD bin sizes

A VisiumHD capture area is 6.5 mm x 6.5 mm. Bin counts at full coverage, with
extrapolated DEG wall times (tissue rarely covers the whole area, so real counts
are typically 50-70% of these):

| Bin size | `--binning-scale` | Bins (full area) | Extrapolated wall time | Under 10 s? |
| --- | --- | --- | --- | --- |
| 2 µm (native) | 1 | ~10,600,000 | hours | ✗ |
| 8 µm | 4 | ~660,000 | ~10 min | ✗ |
| **16 µm (converter default)** | **8** | **~165,000** | **~2 min** | ✗ |
| 32 µm | 16 | ~41,000 | ~26 s | ✗ |
| 64 µm | 32 | ~10,200 | ~5.5 s | ✓ |

**The converter's current default (`--binning-scale 8`, 16 µm) produces roughly
10x more bins than the 10-second threshold allows.** A full-section DEG call on
that data would take on the order of two minutes.

Times beyond 60,000 spots are extrapolations from synthetic data on one
machine, not measurements. Treat them as order-of-magnitude.

### Recommendation

For the demo, use an **appropriately binned or downsampled** dataset:

* `--binning-scale 32` (64 µm bins, ~10k bins) keeps the full section
  interactive at ~5 s per call, or
* keep 16 µm bins but **crop to a tissue sub-region** of roughly 15,000 bins.

64 µm bins are coarse for fine morphology but entirely adequate for
region-level DEG, which is what this module feeds.

### Why this is a patience issue, not a timeout issue

T-042 moves RAG work off the main request path into the background worker.
Once that lands, a slow DEG call shows up as **the user waiting longer for a
chat reply**, not as an HTTP timeout or a failed request. That materially
lowers the severity of everything above — but it also means the wait is
invisible unless the UI reports progress, which is worth considering alongside
T-042.

---

## 6. HANDOFF — Person 6 (pipeline / contracts / routes)

### 6.1 Remove the demo gene fallback (T-044)

**File: `src/rag/pipeline.py` — not mine to edit.**

- **Lines 27-40** define `_DEMO_GENE_OBJECTS`, a hardcoded 12-gene **brain**
  panel (`SNAP25`, `SYP`, `GFAP`, `MBP`, `OLIG2`, ...).
- **Lines 49-52** in `_run_sequential`:
  ```python
  if not gene_objects:
      gene_objects = _DEMO_GENE_OBJECTS
      label = "demo"
  ```

**Why this must change:** the demo dataset named in `README.md` is
**VisiumHD Human Colorectal Cancer**, and `rag.pubmed_retrieval` hardcodes
`disease="colorectal cancer"` in its query builder. So when no ROI is selected
the system currently enriches a *brain* gene panel and searches *colorectal
cancer* literature for it, then presents the result as ROI evidence. That
violates `docs/rules.md` section 1 ("never claim a result was retrieved unless
the API call actually succeeded" / "clearly label fallback results").

**Required replacement behaviour:**
```python
if not gene_objects:
    return {
        "gene_objects": [],
        "context_str": "",           # or an explicit no-context statement
        "metadata": {
            "trace": [], "degs": [], "pathways": [], "citations": [],
            "label": label,
            "status_message": "No gene expression data loaded.",
        },
    }
```
My module already emits exactly that string as `DEGResult.status_message` with
`status == "no_data"`, so it can be propagated rather than re-invented.

### 6.2 Migrate to `run_roi_deg`

`pipeline.py` currently receives `gene_objects` pre-computed by `app.py`. Once
it calls `run_roi_deg` directly it gets FDR filtering on the production path,
which is what satisfies T-009's "genes filtered to adj_pvalue < 0.05". Until
then the legacy wrappers deliberately do **not** filter (see 6.4).

Migration surface:
```python
from rag.deg import run_roi_deg
result = run_roi_deg(h5ad_path, roi_selection, {"fdr_threshold": 0.05})
payload = result.to_dict()          # superset of today's dict
```

**Whoever migrates must read `fdr_applied`.** An unfiltered ranked list handed
to the LLM will be read as "these genes are significant". That boolean is the
guard against exactly that failure.

### 6.3 `contracts.DEGResult` migration

`src/rag/deg/models.py` defines `GeneStat` and `DEGResult` as a **provisional**
local stand-in, explicitly marked as such. When `src/rag/contracts.py` lands:

1. Move `GeneStat` / `DEGResult` into `contracts.py` (or map onto the shared
   equivalents).
2. Delete `src/rag/deg/models.py` and re-point the imports in
   `extraction.py`, `__init__.py`, and `test_deg.py`.
3. Keep `to_dict()` as the boundary format until `pipeline.py`, `routes.py`, and
   `app.py` consume the dataclass directly.

I did **not** create `contracts.py` — it is Person 6's file.

### 6.4 Redundant aliases — safe to remove once contracts land

The per-gene dict carries two pure duplicates, retained only for backward
compatibility:

| Alias | Duplicates |
| --- | --- |
| `mean_roi` | `mean_expression` |
| `pct_roi` | `pct_spots_expressed` |

Nothing in `app.py`, `routes.py`, or `pipeline.py` reads either alias — they are
already dead weight. Grep before deleting, then drop them from
`GeneStat.to_dict()`.

### 6.5 Reconcile `docs/specs.md` section 3.1

specs.md mandates *"Return `None` if h5ad is not loaded"*; T-044 mandates an
empty result carrying `"No gene expression data loaded."` These conflict.

**Resolution implemented:** `run_roi_deg` returns a populated `DEGResult` with
the exact message; the legacy wrappers still return `None`, so
`app/app.py:404`'s empty-state card keeps rendering unchanged. specs.md is not
my file — it needs a line added noting that `run_roi_deg` is the
status-message path.

### 6.6 Demo dataset bin size — configuration item

See section 5a for the measurements. Short version: `run_roi_deg` stays under
10 s up to ~17,000 spots, but `src/convert_feature_slice_h5.py` defaults to
`--binning-scale 8` (16 µm bins), which yields ~165,000 bins on a full VisiumHD
capture area — roughly 10x over that threshold, or ~2 minutes per DEG call.

**Ask:** pick the demo dataset configuration deliberately — either
`--binning-scale 32` (64 µm, ~10k bins, ~5 s) or a cropped tissue sub-region of
~15,000 bins at the current binning. This is a demo-configuration decision, not
a code change, and it interacts with T-042: once RAG work runs in the
background worker, a slow call is a user-patience problem rather than a request
timeout, so consider surfacing progress in the UI.

### 6.7 Config channel into `src/rag/`

The default FDR threshold is read from the **environment variable**
`COPILOT_DEG_FDR_THRESHOLD` (fallback `0.05`), not from `config/app.yaml`.

Reason: `app/config.py` is in the app layer, and `docs/rules.md` section 3
forbids `src/rag/` importing from it. `rag.pubmed_retrieval` sets the same
precedent by reading `PUBMED_*` straight from `os.environ`. A proper config
channel into the RAG layer does not exist and should be designed alongside
`contracts.py`.

---

## 7. HANDOFF — Person 1 (Zainab, preprocessing + clustering)

### 7.1 T-034 is the fix for the normalization gap

T-034 ("store raw counts layer before normalization") is the blocker for
section 3. Its acceptance criterion already says *"DEG Wilcoxon test reads from
it"*, but nothing writes it today.

**What my module needs, concretely:**

1. **Persist a normalized matrix to disk.** `preprocess_adata` currently returns
   an in-memory AnnData that is discarded after clustering. DEG reads the raw
   uploaded file and therefore cannot see any of that work. Writing the
   preprocessed object back (T-035's cache would serve) is the actual
   requirement.
2. **Keep raw counts in `adata.layers["counts"]`** before `normalize_total`, so
   both representations are available.
3. **Record which layer is authoritative** in `spatial_omics.json`, e.g.
   `"deg_layer": "lognorm"`. My module will read the named layer and set
   `expression_source` accordingly instead of inspecting integrality.

Until then DEG runs on whatever was uploaded and honestly labels it.

### 7.2 `preprocess_adata` raises out of the RAG layer

`src/rag/preprocessing.py:44` and `:47` raise `ValueError` for missing
`obsm["spatial"]` and for too-small datasets. `docs/rules.md` section 4 says a
RAG tool must never raise. `run_spatial_clustering` -> `preprocess_adata` means
that exception surfaces in `upload.py`'s background clustering thread.

Reported, not fixed — `preprocessing.py` is not my file.

---

## 8. Deviations from current repo convention

| Deviation | Rationale |
| --- | --- |
| **`logging.getLogger(__name__)` instead of `print()`** | The repo has ~130 bare `print()` calls, which `docs/rules.md` section 2 forbids in production ("do not log API keys or user chat content to stdout"). `src/rag/deg/` uses the logging module throughout. This is intentionally inconsistent with the surrounding code and should spread, not be reverted. |
| **Stdlib `json` / `os.path` instead of `niceview.utils.io`** | `src/niceview/utils/io.py` imports `cv2`, `pandas`, and `toml` at module scope, so `import rag.deg` was pulling in **OpenCV**. For the `.json` / `.h5ad` paths this module touches, `vio.exists` and `vio.load_json` are exactly `os.path.exists` and `json.load` — the only special-casing in `vio.exists` is a cache-miss hack for pyramidal OME-TIFFs, which cannot apply here. Behaviour is unchanged. |
| **`min_cells=0` default on the legacy wrappers** | `min_cells=10` would change which genes today's callers receive. `run_roi_deg` defaults to 10 per T-010; the wrappers default to 0 so the production gene set is untouched until Person 6 migrates. |

---

## 9. Residual niceview coupling (Person 6 item)

`extraction.py` previously did, at module scope:

```python
from niceview.interface.upload import (
    _spatial_omics_state_path, _spatial_omics_cluster_path,
)
```

This violates `docs/rules.md` section 3 (`src/rag/` must not import from the
app layer), reaches into two **private** underscore-prefixed helpers, and forms
an import cycle:

```
rag.deg -> niceview.interface.upload -> app.status_store + rag.clustering
```

**Mitigated within my module** (the coupling itself is not mine to remove):

1. Both resolvers are **injectable** — every public entry point accepts
   `state_path_resolver` / `cluster_path_resolver`. The test suite uses this and
   never touches niceview.
2. The default import is **lazy and function-local**
   (`rag/deg/workspace.py`), so importing `rag.deg` no longer transitively
   imports dash, anndata, `app.status_store`, or OpenCV.

Verified:

```
>>> import rag.deg
dash loaded? False | cv2 loaded? False | app.status_store? False
```

**Proper fix (Person 6):** move `_spatial_omics_state_path` /
`_spatial_omics_cluster_path` into a shared, non-app location — e.g.
`src/rag/workspace_paths.py` or alongside `contracts.py` — and make both
`niceview.interface.upload` and `rag.deg` import from there. Then delete the
lazy shims in `rag/deg/workspace.py`.

---

## 10. Security posture of this module

| Surface | Handling |
| --- | --- |
| `folder_id` path traversal | Whitelist `[A-Za-z0-9_-]{0,64}`. Rejects `..`, `/`, `\`, `C:`, null bytes, spaces. |
| `work_dir` | Must be a non-empty string with no null byte. |
| Final resolved path | `os.path.realpath` on both sides, then a containment check against the workspace root — defeats symlink and `..` escapes even from an injected resolver. |
| `h5ad_path` read from `spatial_omics.json` | Treated as untrusted (it comes off disk): re-validated and containment-checked rather than opened directly. |
| ROI polygon coordinates | Validated for type, finiteness (no NaN/inf), >= 3 vertices, magnitude <= 1e12, and geometric validity. Self-intersecting and zero-area polygons are rejected cleanly. |
| Status messages | Never contain filesystem paths, usernames, or raw exception text — those go to the logger. Messages reach both the UI and the LLM prompt. |
| `pickle` / `eval` / `exec` / `subprocess` | None used. |
| Corrupt `.h5ad` | `ad.read_h5ad` was previously uncaught; now wrapped, with the specific cause logged and the generic T-044 message returned. |

---

## 11. Environment note

The repo pins `requires-python = ">=3.11,<3.12"`, but this machine had only
Python 3.14 and a Store-stub 3.9, so `src/tests/test_deg.py` **skipped** rather
than ran — a green suite meant nothing.

- `conda create -n spatial-copilot python=3.11 -c conda-forge` **failed**:
  conda 4.10.1 (2021) returns `CondaHTTPError: HTTP 000 CONNECTION FAILED`
  against conda-forge, while `curl` reaches the same URL with HTTP 200. The
  client is too old, not the network.
- Fallback used: `pymanager install 3.11` -> **Python 3.11.9**, then a
  project-local `.venv`.
- `pip install -r requirements.txt` **fully succeeded**, contrary to
  expectation — `pyvips 3.1.1` now ships a wheel with libvips bundled, and
  `rasterio` / `opencv` all had cp311 wheels. **Nothing was skipped.**

> **`.venv/` is NOT in `.gitignore`.** The file lists `node_modules/`, `dist/`,
> `build/`, `__pycache__/` and similar, but no venv pattern, so `.venv` shows up
> as untracked. `docs/rules.md` section 1 forbids committing virtual
> environments. `.gitignore` is not mine to edit — **please add `.venv/`
> yourself before staging anything.**

---

## 12. Test coverage (T-011)

`src/tests/test_deg.py` — 72 tests, all executing (not skipping), deterministic
(`SEED = 20240725`), no network, no data files, synthetic AnnData only.

Guard discipline: every `pytest.importorskip` precedes its import.
`src/tests/test_clustering.py` does a bare `import anndata` on line 3 while its
guards sit on lines 8-9, so they never fire and the module errors at collection
instead of skipping — that bug is not repeated here.

Coverage: planted signal; null case (nothing survives FDR); tie correction
against a closed-form value; BH against a hand-computed vector plus
monotonicity and explicit-denominator cases; pre-filter removal and its effect
on the BH denominator; empty ROI, all-spot ROI, single-spot ROI, empty gene
list; missing and corrupt `.h5ad`; 12 malformed-polygon variants; 10 path
traversal variants; backward-compatibility key and bit-for-bit value locks for
both legacy wrappers; and a JSON-serialisability check guarding against numpy
scalars leaking into session JSON.
