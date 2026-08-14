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

The reader gate proves that all 200 selectors remain represented, one completed Page is readable while siblings are still processing, all 200 eventually complete, unvisited pages do not fetch images, stale navigation cannot replace the current Page, superseded Blob URLs are revoked, and unmount revokes the final active Blob URL. It runs desktop Chromium, a 390x844 mobile viewport, native keyboard navigation, and the repository's Axe-based accessibility check for serious/critical violations.

Admission-to-`200/200` completion must remain at or below 120 seconds. The whole workflow job has a five-minute timeout and zero Playwright retries.

## Machine-readable evidence

A successful run uploads `large-document-e1-evidence` with:

- the workload manifest;
- the non-secret document marker;
- initial and final DB/query diagnostics;
- browser timing/request/Blob/accessibility metrics;
- dedicated runtime HTTP counts;
- queue fairness characterization;
- a compact combined result manifest.

The aggregate projection diagnostic records SQL statement count and Job rows loaded. Slice E1 measures the existing set-oriented query shape rather than changing it.

The queue probe uses the real PostgreSQL claim/recovery path for a 200-job Document A followed by standalone work, a small Document, and an expired/recovered retry. Its full claim sequence and ownership are evidence only. It intentionally does not assert a fairness quantum or modify queue ordering/indexes; scheduling policy remains deferred to Slice E4.

## Explicit non-goals

This harness does not implement retention bounded-work changes (E2), PDF resource/restart changes (E3), queue fairness policy (E4), or final Compose acceptance (E5). It does not run the 480 MiB pressure workload, production OCR models, Gemini, or copyrighted manga content. It does not change production upload/security/capability/retention/queue semantics.
