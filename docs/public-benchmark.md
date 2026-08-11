# Public OCR benchmark evaluator

MangaSensei's public OCR benchmark keeps ground truth, OCR execution and scoring as separate artifacts. The v1 scorer consumes the frozen [MangaSensei Public Demo Corpus v1](../assets/public-demo/README.md) plus a separately captured OCR observation and emits a deterministic benchmark report. It never runs OCR, Gemini or any network service.

```text
frozen public-demo ground truth
+ frozen OCR observation JSON
-> deterministic offline evaluator
-> versioned benchmark report JSON
```

This separation allows the same OCR observation bytes to be rescored by later evaluator versions without rerunning models or changing authored ground truth. Real-local-OCR observation generation is intentionally a separate follow-up slice.

## Contracts and provenance

The versioned [observation v1 schema](../scripts/public_benchmark/schemas/observation-v1.json) is independent from the evaluator version. It binds an observation to the exact corpus using the corpus ID/schema version, raw `manifest.json` SHA-256, annotation-schema SHA-256 and, for every manifest page, the page ID, image SHA-256, annotation SHA-256 and dimensions.

Observation provenance mirrors information the current production `OcrResult` can expose or a capture runner can record without changing OCR behavior: producer repository SHA and MangaSensei version, OCR contract identity, detector/recognizer identity, model-manifest version/hash, OCR configuration digest, upstream repository/commit, model artifact hashes when supplied, runtime library/device/platform provenance when supplied, and the capture timestamp. The timestamp is provenance only and is never copied into a scorer-generated timestamp.

Each observed region carries its deterministic observation ID, raw integer pixel bbox, optional raw polygon, angle, confidence, exact raw OCR text and final reading-order index. Observation v1 explicitly declares that the current OCR output does **not** support presentation marks, ruby-to-base relationships, predicted text roles or linguistic/JMdict output. Secrets, API keys, capability tokens, usernames, hostnames, private manga data and unnecessary absolute paths do not belong in observations.

The versioned [report v1 schema](../scripts/public_benchmark/schemas/report-v1.json) records the metric-spec/evaluator version and repository SHA, source-observation schema/hash/provenance, corpus hashes, scoring configuration, per-page detail, aggregate count-based metrics, unsupported families, warnings and a concise `publicSummary`. Reports do not copy GT/OCR transcription strings; those remain owned by the separately hash-addressed corpus and observation.

Corpus loading fails closed before scoring if the frozen manifest, annotation schema, page inventory, image bytes/dimensions, annotation bytes or observation bindings disagree. `intendedUseCases` is not used for scoring.

## Matching v1

Primary geometry is the axis-aligned bbox IoU. For a GT box `G` and observation box `O`:

```text
IoU = intersection_area / union_area
```

A pair is eligible at IoU >= 0.50, but eligibility is decided with integer pixel arithmetic:

```text
2 * intersection_area >= union_area
```

The evaluator then performs a global one-to-one assignment with a deterministic Hungarian-style implementation using only the Python standard library. The objective is lexicographic: maximize the number of eligible matches first, maximize aggregate exact IoU second, then use stable sorted GT/observation IDs and fixed traversal for remaining equal-cost ties. Complexity is `O(n^3)` time and `O(n^2)` memory. Greedy matching is not used.

Consequently, one observation can satisfy at most one GT region and one GT region can consume at most one observation. Duplicate/split detections leave extra fragments as false positives; a merged detection can satisfy at most one GT region; sub-threshold overlap remains unmatched. Reports retain integer intersection/union areas and IoU in integer parts-per-million (`ppm`) for diagnostics. Optional polygons are diagnostic only and do not affect v1 matching.

Detection-unscored positive GT regions contribute neither TP nor FN. A matched observation may be ignored against such a region, and an otherwise unmatched observation may be `ignored_unscored_ground_truth` when at least 50% of its own bbox area lies inside a detection-unscored GT bbox.

## Detection metrics

Detection is computed only from detection-scored GT:

- `TP`: scored GT with a one-to-one spatial match;
- `FN`: scored GT without a match;
- `FP`: unmatched observations after explicit detection-unscored ignoring.

Precision, recall and F1 retain numerator and denominator together with a deterministic six-decimal representation. Undefined denominators use explicit statuses such as `insufficient-data` or `not-applicable`; the evaluator never substitutes a synthetic 0% or 100%.

## Recognition metrics

Recognition is conditional on the spatial match. A recognition-scored GT region is either scored against its single matched observation or recorded as a recognition coverage miss. The evaluator never concatenates or reconstructs text from neighboring OCR detections to obtain a transcription score.

`strict-nfc-v1` preserves authored GT exactly and requires GT `transcription.raw` to already be NFC; non-NFC GT is a contract error. Raw observation text is preserved as emitted, while only its comparison representation is NFC-normalized. The evaluator does not apply NFKC, width folding, kana folding, punctuation removal, whitespace trimming/collapse, dictionary correction or spelling correction. Thus `７番線` and `7番線` remain different.

Levenshtein distance is over Unicode code points. The report includes recognition coverage, exact-match count/rate, total edit distance, total GT characters, micro CER and macro CER for all recognition-scored text plus separate base and ruby slices. CER is not clamped to 1.0. Environmental text and SFX remain positive OCR text. Recognition numbers should always be presented with recognition coverage.

## Reading order

Reading-order evaluation uses only GT regions with `readingOrder=true` that have valid one-to-one spatial matches. It reports order coverage, comparable pair count, inversion count, **pairwise ordering accuracy on matched regions**, and normalized Kendall-style inversion distance.

Exact page sequence is measured only when every order-scored GT region is matched. With incomplete region matching the report uses `exactSequence.status = "not-measured"` and reason `incomplete-region-matching`. Extra OCR regions remain detection false positives and receive no second ordering penalty. The evaluator does not invent panel IDs because current production observations expose final region order, not predicted panel identity.

## Graphical negative zones

Negative-zone scoring is separate from global detection precision. For every observed bbox `O` and graphical negative zone bbox `Z`, the evaluator computes observation coverage (`intersection / observation_area`) and zone coverage (`intersection / zone_area`). A pair is a hit when either coverage is at least 0.50.

The report includes zone totals/hits/rate, hit-pair count, unique observed regions hitting zones, per-zone observation IDs, hit-pair detail and maximum observation/zone coverage. Lexical OCR text is irrelevant. Multiple detections can create multiple hit pairs while a zone contributes at most once to `zonesHit`.

## Explicitly unsupported families

Observation v1 cannot support every authored corpus concept. The report marks these families `unsupported` rather than reporting 0%:

- presentation marks / bōten;
- ruby -> base furigana relationship correctness (base/ruby region recognition itself is measurable);
- text-role classification (GT roles may only appear as recognition diagnostics);
- linguistics, tokenization, JMdict identity/senses and dictionary fallback behavior.

## Deterministic output

For identical corpus bytes, observation bytes, evaluator version and evaluator repository SHA, report bytes are identical. Page order follows the manifest; matches and GT detail are sorted by GT ID; unmatched/ignored observations and negative-zone detail use stable IDs; JSON keys are sorted; UTF-8 is emitted with `ensure_ascii=False`, fixed indentation and one trailing newline. The scorer generates no timestamp.

The CLI writes through a temporary file and atomic replacement, so a validation/scoring failure does not leave a partially valid-looking requested output.

```bash
uv run python scripts/public_benchmark/evaluate.py \
  --corpus assets/public-demo \
  --observations path/to/observation.json \
  --output path/to/report.json
```

`--evaluator-repository-sha` may be supplied explicitly; otherwise the CLI resolves the current repository SHA from CI environment variables or local Git metadata. There is deliberately no `--run-ocr` mode. Scoring uses only the Python standard library and local files and requires no new dependency or network access.

## Public claim boundary

A benchmark result may be described only with its dataset scope, for example:

> On MangaSensei Public Demo Corpus v1, ...

Do not turn this four-page project-authored corpus into generic claims such as “MangaSensei OCR accuracy is X%”, “Japanese manga OCR accuracy is X%” or “state of the art”. Do not create a single blended score, and do not rename `100 * (1 - CER)` as generic accuracy. Detection, recognition coverage/transcription quality, reading order and graphical negative-zone behavior are distinct measurements.

This evaluator foundation commits no production OCR observation and no benchmark result. Synthetic observations used by tests are regression fixtures, not product evidence. The next slice is a separately reviewed real-local-OCR observation runner for the frozen public corpus, followed by the first real observation/report pair.
