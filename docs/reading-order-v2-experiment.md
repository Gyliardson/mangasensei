# Reading Order v2 experiment infrastructure

Reading Order v2 is a post-merge reading-order research experiment. PR1 adds infrastructure only; it does **not** change production OCR ordering behavior and produces no quality result.

The frozen baseline is repository SHA `292f0a8c8142d919ac4184159d102789c43b4116`. The machine-readable methodology is [experiment-spec-v1.json](../scripts/reading_order_v2/spec/experiment-spec-v1.json), and the pre-authoring held-out layout contract is [corpus-design.json](../assets/reading-order-v2/heldout-v1/corpus-design.json).

## Boundary

The experiment receives project-authored RGB page pixels plus frozen post-merge `TextBlock`-equivalent regions and returns the same regions in a deterministic order plus geometry-only diagnostics.

Detector, recognizer, `textline_merge`, PP-OCRv6, OCR text semantics and confidence-based ordering are outside the experimental variable set. The fixture loader constructs the real vendored `TextBlock` with the same opaque text sentinel and probability for every region so orientation is derived from geometry rather than semantic content.

## Arms

The only permitted arms are:

- `A0_B0_CONTROL`: exact production panel-flow-v1 and exact production `manga_tier_order`.
- `A1_B0_PANEL_ONLY`: frozen partial-panel-evidence policy with production local order.
- `A0_B1_ORDER_ONLY`: production panel evidence with frozen orientation-aware local ordering.
- `A1_B1_COMBINED`: both experimental variables.

Production calculations remain authoritative for A0. [reading_order.py](../backend/src/mangasensei/ocr/reading_order.py) exposes behavior-preserving internal seams for the tier partition, strict center containment, precedence-edge construction and deterministic topological scheduling. Experimental composition is isolated in [reading_order_v2.py](../backend/src/mangasensei/ocr/diagnostics/reading_order_v2.py); normal OCR does not import or persist these diagnostics.

## Diagnostics

`reading-order-v2-diagnostic-v1` records the actual segmentation, assignment membership, precedence edges, fallback state, local tier/run selection and final order. It never serializes Python object IDs. Current `PanelBox` has no polygon, so group `polygon` is explicitly `null` rather than reconstructed.

If panel segmentation produced boxes but marked them unreliable, diagnostics still record strict-center candidate memberships derived from those actual boxes. This is observation-only: assignment remains non-confident/not-attempted and ordering fallback behavior is unchanged.

Group `tieKey` describes the actual key consumed by the applicable A0/A1 topological scheduler when that scheduler runs. The canonical JSON string `"inf"` represents the scheduler's positive-infinity fallback-rank sentinel; an empty `tieKey` means no group scheduler ran.

The schema is [diagnostic-v1.json](../scripts/reading_order_v2/schemas/diagnostic-v1.json).

## Corpus separation

There are three separate evidence tiers:

1. Unit/harness fixtures under `tests/reading_order_v2/` prove implementation mechanics only.
2. The existing Public Demo corpus is calibration material only. This PR does not run it and its future calibration result cannot qualify Reading Order v2.
3. `mangasensei-reading-order-heldout-v2` v1.0.0 is the future qualification corpus. PR1 freezes only its 16 page slots; final H01-H16 source/images/inputs/annotations belong to a later PR2.

Arm-visible assets and scorer-only GT are physically separated. The future arm runner derives only `images/Hxx.png` and `inputs/Hxx.json`; it has no annotation argument. Scoring loads `annotations/Hxx.json` only after arm outputs exist.

Qualification-pair slices are fail-closed to the frozen inventory: `A`, `B`, `A+B`, `clean-control`, `vertical-only`, `horizontal-only`, `mixed`, `partial-assignment`, and `intentional-fallback`. Unknown slices are invalid. Final corpus validation requires each frozen slice to contain non-empty comparable qualification-pair evidence. The existing A/B pair/page minima and layout minima are unchanged.

## Frozen held-out authoring chronology

After PR1 is approved and merged:

1. Author H01-H16 without executing any arm.
2. Render deterministic 1440x2048 SVG pages with [render.mjs](../scripts/reading_order_v2/render.mjs). Pages must contain no semantic text nodes or external image/network content.
3. Validate arm-visible fixture geometry and scorer-only GT.
4. Freeze all corpus hashes with [freeze.py](../scripts/reading_order_v2/freeze.py).
5. Validate the final corpus with [validate_corpus.py](../scripts/reading_order_v2/validate_corpus.py).
6. Merge/freeze PR2 before any qualification execution.

Changing a page or GT after observing candidate output contaminates v1 and requires a new corpus version.

## Scoring and gates

Reading-order scoring uses corpus-owned region IDs; no OCR/IoU matching is performed for reading-order metrics. Formal comparisons use integers and `Fraction`. Decimal strings are presentation-only.

Panel diagnostics are separate and reuse the existing public-benchmark deterministic IoU >= 0.50 one-to-one matcher. They do not influence A1 behavior.

The formal verdict engine can emit only:

- `READING_ORDER_V2_HELDOUT_PASS`
- `INVALID_EXPERIMENT`
- `A_FAIL`
- `B_FAIL`
- `COMBINED_FAIL`
- `A_INCONCLUSIVE`
- `B_INCONCLUSIVE`

A candidate may introduce no new held-out wrong pair. Universal non-regression is evaluated explicitly across the complete frozen required-slice inventory, and every required control/candidate slice must contain comparable evidence. A and B each require their predeclared mechanism to be exercised and require strict improvement on their predeclared qualification-pair slice; otherwise the result is failure or inconclusive, not a tuned retry.

Formal qualification uses [comparison.py](../scripts/reading_order_v2/comparison.py) as the machine-derived orchestration layer. Callers do not supply `harness_valid`, `a_exercised`, or `b_exercised` switches. The layer validates the frozen corpus and exact arm artifacts, region integrity, required diagnostics/slices, three-repeat determinism and selected-artifact hash binding before it derives exercise state and invokes the low-level verdict evaluator.

A exercise requires actual A/A+B diagnostic evidence for reliable segmentation, at least two confidently populated panel groups, an uncertain region, A0 assignment-semantic whole-page fallback and A1 partial panel evidence actually used on the same qualifying page.

B exercise requires actual diagnostics plus frozen qualification declarations showing successful panel evidence with horizontal-LTR behavior, mixed-orientation behavior and vertical-RTL control evidence. A page label alone is not sufficient.

## Determinism and future evidence

Canonical JSON is UTF-8/LF, key-sorted, and independent of dictionary insertion order. Qualification requires three fresh-process repetitions per arm. Every repeat is scored after its arm output exists, and `repeat-hashes.json` records independent canonical SHA-256 values for diagnostics, ordering and `CorpusScore`. All three hash classes must match across all three repeats before one repeat's canonical score may be published. Latency/timestamps are not part of output equality.

The future evidence writer validates an allowlisted logical contract plus declared exact harness source snapshots, verifies checksums, rejects symlinks/private absolute paths/secret-like assignments/model or image binaries, and writes ZIP members in relative POSIX lexical order. Path validation is structural for path/provenance fields with textual defense in depth: generic Windows drive-root and private local POSIX absolute paths are rejected, while repository-relative POSIX paths and normal HTTP(S)/JSON-schema URLs remain valid.

Future provenance must distinguish the immutable baseline SHA from the exact implementation/corpus execution SHA.

## Validation commands for PR1 infrastructure

These commands validate infrastructure; they do not execute OCR or held-out quality:

```bash
uv run python -m scripts.reading_order_v2.validate_design
uv run pytest tests/unit/test_ocr_panel_reading_order.py tests/reading_order_v2 tests/public_benchmark
uv run ruff check backend/src tests scripts
uv run mypy backend/src
uv run pytest --cov --cov-report=term
uv build
```

Do not run `run_heldout.py` until PR2 has been separately authored, reviewed, merged and the qualification run has been explicitly authorized.

## Calibration and later gates

The frozen future order is: structural/unit validation -> new held-out v2 qualification -> Public Demo characterization -> authorized real-manga Black Jack regression gate. Historical surrogate assertions are not Black Jack evidence.

The prior PP detector + current48 + current downstream v1 result remains **NO-GO**. Reading Order v2 infrastructure does not retroactively alter that result and does not requalify PP-OCRv6.
