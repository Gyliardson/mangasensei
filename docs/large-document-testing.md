# Large-document Slice E1 harness

Slice E1 of [#105](https://github.com/Gyliardson/mangasensei/issues/105) is a deterministic maximum-cardinality control-plane/full-stack gate. It is intentionally separate from the normal full-stack browser runtime so the 200-page workload cannot consume the request headroom reserved for the ordinary suite.

The dedicated [Large Document E1 workflow](../.github/workflows/large-document.yml) uses fresh PostgreSQL state, local storage, API rate-limit state, a dedicated worker process, and one Chromium browser context. The API keeps the production/default `120/min` general rate limit; E1 does not use the normal full-stack suite's test-only `240/min` override.

## Frozen workload

`tests.large_document.generator` generates `CONTROL_PLANE_MAX_200` at runtime. No 200-page binary fixture set is committed.

For zero-based page ordinal `i = 0..199`:

- dimensions are `80x120` RGB;
- color is `(i % 256, (73*i) % 256, (151*i) % 256)`;
- every PNG row uses filter byte `0`;
- zlib compression level is `9`;
- the PNG contains deterministic `IHDR`, `IDAT`, and `IEND` chunks;
- the filename is `page-{i+1:06d}.png`.

The frozen aggregate is 200 pages, 1,920,000 pixels, 39,780 encoded bytes, a 108–201 byte encoded-page range, and 200 unique image contents. The SHA-256 of the ordered concatenation of all encoded page bytes is `c60a3b6c1cf2e2219be89286fc917ccc87d89b2b23e84449f4d83e589b60008b`.

The generator fails instead of accepting changed bytes when those values drift. Its JSON manifest also records each generated filename, ordinal, RGB tuple, encoded size, and SHA-256.

## Worker boundary

`tests.large_document.worker` composes the real `mangasensei.workers.runner.Worker` with the real PostgreSQL queue, claim/attempt/lease/fencing paths, local immutable image storage, OCR persistence, linguistic-run persistence, and StudyResult completion.

Only the external OCR engine is replaced. `DeterministicLargeDocumentOcr` reads and hashes the real persisted image bytes, rejects any image outside the frozen workload, and returns a stable empty-region OCR result. Empty regions deliberately minimize CPU work while still traversing the production Page worker stages. Gemini is disabled and no network provider is invoked. The worker introduces no per-page sleep.

The browser scenario does not start this worker until a PostgreSQL diagnostic has proved the complete initial graph: one Document, 200 ordered Pages, 200 initial Jobs, 200 unique image blobs, four document capabilities, and `0/200` completion.

## Browser and request contract

The dedicated Playwright scenario uploads exactly one 200-page Document through the real browser UI and then uses only aggregate Document polling. The hard browser API model is:

- one `POST /api/v1/documents`;
- at most 60 aggregate Document GETs;
- StudyPage GETs only for sampled pages 1, 100, and 200;
- protected image GETs only for those same sampled pages;
- no unexpected HTTP 429 response.

The resulting maximum classified browser API envelope is 67 requests, below the unchanged production/default 120/min limit. The workflow also parses the dedicated API log independently and fails on any 429.

The partial-readability proof is temporally linked rather than inferred from unrelated historical observations. Before releasing the worker, the harness selects page 200 while every Page is still processing. It then waits for one live aggregate projection in which page 1 is explicitly `resultAvailable` and siblings are still processing, records that page identity and progress, selects that exact page, waits for its real StudyPage and protected-image responses, and verifies the rendered frozen image. Immediately after that successful render it performs a fresh aggregate GET and requires `processingPages > 0` again. This proves siblings remained in progress through the successful page-1 read operation, not merely at some earlier point in the run.

The reader gate also proves that all 200 selectors remain represented, all 200 eventually complete, unvisited pages do not fetch images, stale navigation cannot replace the current Page, superseded Blob URLs are revoked, and unmount revokes the final active Blob URL. It runs desktop Chromium, a 390x844 mobile viewport, native keyboard navigation, and the repository's Axe-based accessibility check for serious/critical violations.

Admission-to-`200/200` completion must remain at or below 120 seconds. The whole workflow job has a five-minute timeout and zero Playwright retries.

## Machine-readable evidence

A successful run uploads `large-document-e1-evidence` with:

- the workload manifest;
- the non-secret document marker;
- initial and final DB/query diagnostics;
- browser timing/request/Blob/accessibility metrics;
- linked `partialReadability` evidence containing the selected page ID/ordinal, before-read progress, exact StudyPage/image read results, rendered-page identity, and fresh after-read progress;
- dedicated runtime HTTP counts and sampled Page identities;
- queue fairness characterization;
- a compact combined result manifest.

`tests.large_document.ci_metrics` fails closed unless the selected Page was result-available in the before-read projection, both protected reads belong to that same Page and succeeded with the expected capability headers, the rendered frozen RGB content matches that Page ordinal, and both the before-read and fresh after-read projections still contain processing siblings. It also cross-checks the sampled Page identities against the independent server-side request trace. Capability values themselves are never written to evidence.

The aggregate projection diagnostic records SQL statement count and Job rows loaded. Slice E1 measures the existing set-oriented query shape rather than changing it.

The queue probe uses the real PostgreSQL claim/recovery path for a 200-job Document A followed by standalone work, a small Document, and an expired/recovered retry. Its full claim sequence and ownership are evidence only. It intentionally does not assert a fairness quantum or modify queue ordering/indexes; scheduling policy remains deferred to Slice E4.

## Slice E2 bounded retention

Slice E2 hardens the existing 24-hour retention janitor so cleanup work is bounded by Page rows rather than by a bounded number of parent Documents with unbounded child fan-out.

For `RetentionJanitor.run_once(batch_size=N)`, the janitor selects at most `N` retention-eligible Pages. A standalone Page is eligible when its own `expires_at` has passed; a Document-owned Page is eligible when its parent Document has expired, even if that child Page has a later `expires_at`. The selected Page rows are ordered deterministically and locked with PostgreSQL `FOR UPDATE SKIP LOCKED` before Gemini abandoned-call reconciliation and direct Page deletion.

Expired Documents are physically deleted only after they contain zero Pages, and that empty-Document cleanup is separately bounded. A large expired Document can therefore drain over several janitor cycles instead of cascading all child Pages in one transaction. This does not extend access lifetime: document authorization already rejects an expired Document while remaining child rows are still being physically drained.

Image-blob cleanup is also bounded to at most `N` `_delete_if_unreferenced()` attempts per cycle. Blobs made newly orphaned by the selected Page batch are prioritized, remaining budget can service older orphan backlog, and the existing digest lock, reference re-check, `deleting` state, upload serialization, and filesystem-before-final-row-delete protocol remain unchanged. If the process exits after Page deletion commits but before filesystem cleanup, the orphan ImageBlob remains discoverable by a later janitor cycle.

Stale `RateLimitBucketRecord` cleanup keeps the existing one-day eligibility threshold but now selects and deletes at most `N` oldest buckets per cycle with row locking. Active/current buckets and configured request rate-limit semantics are unchanged. Pending storage-write reconciliation was already limited by the same `batch_size` and remains bounded.

These changes keep the retention/access contract at exactly 24 hours while making the load-bearing cleanup classes `O(batch_size)` instead of `O(batch_size * pages_per_document)`.

## Slice E3 PDF scale and recovery

Slice E3 adds evidence-first maximum-page, resource, pressure, and crash/reclaim coverage around the Slice-D PDF importer. The permanent gates are [PDF Scale E3](../.github/workflows/pdf-scale.yml) and [PDF Import Pressure E3](../.github/workflows/pdf-pressure.yml). They execute the raw PR-head/source SHA explicitly and upload JSON evidence only; no generated 200-page PDF or 480 MiB raster set is committed or uploaded.

### Source provenance repair and frozen workload

An early Slice-E planning draft listed a 49,198-byte PDF with SHA-256 `02f15ab6368b3f32e86a701e0fbb4d6114d98fd0e71590c3c27d8c225b54a33d`. That planning note did not contain or reference a byte-level serializer sufficient to reproduce the claimed bytes. Before any E3 raster calibration, resource measurement, recovery conclusion, or production decision, that incomplete planning-only identity was withdrawn. It was not a production regression or a historical runtime artifact.

The repository-tracked source of truth is now [`pdf-scale-stdlib-v1`](../tests/pdf_scale/generator.py). The stdlib-only serializer emits PDF 1.4 with LF line endings, fixed object numbering/xref/trailer serialization, no metadata/IDs/compression/active content, exactly 200 Pages with a `28.7 x 43.0` point MediaBox, and one deterministic 1x1-point black rectangle per Page. Its frozen source identity is:

- workload: `PDF_PAGECOUNT_MAX_200`;
- source bytes: 46,282;
- source SHA-256: `cb181b41e45a46e138b7188d87d54620e4c1738dd654f3e6cb7eadc854ef2cf5`;
- Page ordinals: `0..199`.

Regeneration tests fail on any byte-count or digest drift and also open the generated source with the reviewed PDFium runtime to verify the 200-page geometry. No resource conclusion in E3 was based on the superseded planning source identity.

### Reviewed raster calibration

The first authoritative pinned-runtime run was calibration only, on repository source SHA `a56e69c055c8b242f90f5d05a780b07af342b340`. The calibrated values were then frozen in [`raster-contract.json`](../tests/pdf_scale/raster-contract.json) before post-calibration evidence ran.

Under `pdfium-raster-v1` with pypdfium2 5.12.1, PDFium build 7947 (`152.0.7947.0`), Pillow 12.3.0, the normal non-V8/non-XFA PDFium build, fixed 200 DPI rendering, white background, RGB PNG and compression level 6, the workload produces:

- 200 ordered `80x120` rasters;
- 1,920,000 aggregate pixels;
- 48,223 aggregate encoded raster bytes;
- 236-byte minimum and 244-byte maximum raster;
- ordered concatenated-raster SHA-256 `275ff16afad710b8d509f5038a57e65ed9952ba5e371a8cbdb84f94b6dfe4bff`.

All 200 individual raster hashes are part of the frozen contract rather than being summarized here.

### Resource envelope observations

The E3 scale topology keeps the production renderer at 1 GiB / 1 CPU / 64 PIDs and the PDF importer at 1536 MiB / 1 CPU / 64 PIDs. It does not start the OCR/Page worker or Gemini.

A qualifying post-fix hosted-run observation on source SHA `2408f637c949508133b77a1293b2cc42b89aaeab` completed the clean 200-page import with zero durable Document/Page/Job rows at the manifest checkpoint and exactly 1 Document / 200 Pages / 200 pending initial Jobs after commit. The renderer observed a 246,251,520-byte cgroup-v2 memory peak (22.93% of 1 GiB); the importer observed 71,913,472 bytes (4.46% of 1536 MiB). Both `memory.events` records had zero OOM/OOM-kill/max events and Docker reported `OOMKilled=false`. These are run observations, not product performance SLOs.

The same clean run observed approximately 3.199 s renderer time, 0.062 s manifest validation, 1.209 s commit, and 6.680 s admission-to-terminal cleanup. At manifest completion the source occupied 46,282 bytes, the request 432 bytes, and the renderer-output import contained 201 files / 94,397 bytes; terminal cleanup removed source, request, and renderer-output import artifacts.

The separate `PDF_IMPORTER_PROTOCOL_PRESSURE_480M` profile substitutes only the renderer-output protocol boundary. It generates 60 valid unique `80x120` RGB PNGs of exactly 8,388,608 bytes each using a deterministic private ancillary chunk, for 503,316,480 aggregate bytes (480 MiB) and 576,000 aggregate pixels. The real coordinator still validates spool identity, hashes, Pillow decoding, aggregate limits, lease/fence ownership, storage staging, Document/Page/Job creation, and terminal cleanup. This profile does not prove renderer resource usage.

A qualifying hosted pressure run observed a 1,101,508,608-byte importer peak against the unchanged 1,610,612,736-byte limit (68.39%), with zero OOM/OOM-kill/max events and `OOMKilled=false`. It completed exactly 1 Document / 60 Pages / 60 pending initial Jobs / 60 unique immutable image blobs, preserved all raster hashes, and removed terminal spool artifacts. The predeclared E3 engineering review threshold is 80%, so this run did not require a memory-architecture redesign or cgroup increase.

### Recovery behavior and production correction

A real renderer process/container failure during an active 200-page import remains fail-closed: the import terminates with `pdf_renderer_crash`, no durable Document/Page/Job graph exists, transient spool state is cleaned, and a fresh import succeeds after the renderer is restarted.

E3 did expose one recovery defect after a renderer had already completed fence 1 and the importer then crashed. After lease expiry/reclaim, the importer correctly acquired fence 2 and published a fence-2 request, but the split renderer input channel is intentionally read-only to the renderer, so the stale fence-1 request remained. The renderer selected request filenames in sorted order, repeatedly encountered the already-terminal fence-1 request, returned, and starved the newer fence-2 request.

The production correction is intentionally narrow: in split-output mode, the renderer now skips request entries whose matching manifest or failure record already exists and continues scanning later requests. It does not delete or mutate the read-only input channel, change fencing authority, reuse partial raster work, increase timeout/lease/memory limits, or weaken spool validation. A focused renderer regression covers stale terminal fence 1 followed by fence 2, and the full E3 reclaim scenario proves that the higher fence completes exactly one 200-Page Document while stale ownership cannot commit.

The post-DB-commit/pre-cleanup recovery boundary is also covered with a test-only seam only around `_cleanup_terminal_source()`. `_commit_document()` remains real. After simulated process death, the completed 1/200/200 graph remains durable with `source_cleaned_at` unset; a fresh coordinator's normal cleanup removes transient source/request/attempt state, sets `source_cleaned_at`, and preserves the committed Document and all 200 Pages/Jobs.

E3 therefore required one focused renderer request-selection fix. It did not require renderer/importer memory increases, a longer 180-second renderer timeout, a longer 240-second import lease, relaxed raster/spool limits, OCR/Gemini execution, or partial user-visible PDF Documents. Slice E4 queue fairness and Slice E5 whole-production-topology/resource acceptance remain deferred under #105.

## Explicit non-goals

The E1 harness does not run the 480 MiB pressure workload, production OCR models, Gemini, or copyrighted manga content. Slice E2 does not change production upload/security/capability semantics beyond bounded physical cleanup. E3 does not change queue fairness, API/worker/retention cgroups, OCR/Gemini behavior, reader UX, retention duration, or the existing renderer isolation/security boundary. Slice E4 (queue fairness policy) and Slice E5 (final whole-production-topology/resource acceptance) remain deferred.
