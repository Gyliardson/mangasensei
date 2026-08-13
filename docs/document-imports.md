# Document imports

MangaSensei supports either an ordered set of JPEG, PNG, or WebP images or one PDF source as a temporary `Document`. PDF import uses a distinct transient asynchronous render/import resource; only after every raster page passes the existing image-validation contract is the ordinary ordered Document/Page/Job graph committed.

## Resource model

A document is an aggregate over the existing page-processing model:

```text
Document
  -> ordered Page
     -> independent page_analysis Job / Attempt
     -> independent StudyResult
```

`Page` remains the atomic OCR, linguistic-analysis, study-result, retry, and language-reprocessing unit. There is no document-wide OCR job, combined OCR result, or cross-page reading order.

The original image of every page is preserved. Identical image content may share one immutable `ImageBlobRecord`, but repeated images still create separate ordered Page memberships and separate initial jobs.

## Creating a document

`POST /api/v1/documents` accepts multipart form data:

- repeated `images[]` parts in the desired initial order;
- one `studyLanguage` (`pt-BR` or `en`);
- an `Idempotency-Key` header.

The multipart sequence is the canonical initial order. Filenames, EXIF timestamps, and lexical filename ordering are not used to infer manga order.

One selected image continues to use the standalone `POST /api/v1/pages` flow in the SPA. Two or more selected images use `POST /api/v1/documents`.

The browser lets the user inspect, move up/down, remove, or clear selected images before upload. Reordering is available through ordinary keyboard-focusable buttons and does not require drag-and-drop reordering.

## PDF import (Slice D)

`POST /api/v1/document-imports` accepts exactly one `application/pdf` part named `pdf`, one `studyLanguage` and an `Idempotency-Key`. A new request returns HTTP `202` with a transient import UUID, `pdfium-raster-v1`, expiry and a `read:document-import` capability. The browser polls `GET /api/v1/document-imports/{importId}` with that capability in `X-Document-Import-Token`. It does not put import or Document capabilities into URLs, history or `localStorage`.

The import status is deliberately separate from normal Page `JobStatus`:

```text
PDF import: queued -> rendering -> completed | failed
Document child: ordinary Page -> ordinary page_analysis Job / Attempt -> StudyResult
```

`completed` is the only import state that exposes a logical Document. The status response then returns the normal Document UUID plus freshly issued `read:document`, `read:document-image`, `reprocess:document` and `manage:document` capabilities. The SPA immediately reuses the existing Document reader/progress/recovery flow. A failed import returns a terminal stable PDF error code and no partial Document.

### Renderer trust boundary

The production renderer is a separate non-root Compose service with a distinct Unix identity. Coordinator/application processes run as `10001:10001`; the renderer runs as `10002:10002` and receives only supplementary group `10001` so it can read coordinator-published inputs and write specifically prepared renderer-output leaves. The coordinator owns the input/control root, request/source parents, renderer-output root, import parents and attempt parents. Those trusted parents are not renderer-writable. Only the pre-created attempt leaf and renderer-heartbeat leaf are writable by the renderer.

The channels are separate mounts. The renderer mounts `/app/var/pdf-spool` read-only for source/request input and `/app/var/pdf-renderer-output` writable for manifests, rasters, failures and heartbeat. It does not mount `/app/var/storage`. The API mounts only the coordinator input/control channel; the privileged PDF importer mounts both channels plus application storage because it is the component that validates renderer output and publishes ordinary Page image blobs. The trusted `pdf-spool-init` service establishes the channel topology as UID/GID `10001:10001` and remains mounted as a network-isolated lifetime anchor for the bounded tmpfs volumes.

Renderer output remains attacker-controlled and is never trusted by pathname. Privileged consumption walks the renderer-output tree descriptor-relatively using `dir_fd`/openat-style traversal, opens trusted directories with `O_DIRECTORY | O_NOFOLLOW`, opens final files with `O_RDONLY | O_NONBLOCK | O_NOFOLLOW`, requires a regular file, link count at most one and a bounded size using `fstat`, and reads bytes from that same validated file descriptor. Device/inode identity is revalidated after consumption and raster bytes are pinned before later validation/persistence. Parent or final-path replacement after open cannot redirect the bytes being consumed. Cleanup is descriptor-relative and unlinks renderer-authored symlinks instead of following them.

The renderer has no database, capability pepper, OCR model, JMdict or Gemini configuration; no application-storage mount; no Docker socket; `network_mode: none`; `cap_drop: ALL`; `no-new-privileges`; a read-only root filesystem; bounded `/tmp`; one CPU; a 1 GiB memory limit; and a PID limit. PDF rendering is sequential and each request executes inside a disposable subprocess supervised by the renderer service, so a native crash, signal or wall-clock overrun cannot be mistaken for a successful manifest. These controls reduce renderer reach but do not make renderer output trusted; the importer remains responsible for validating every renderer-controlled byte before commit.

The supported runtime is pinned to `pypdfium2 5.12.1` with bundled PDFium `152.0.7947.0` build `7947`. Startup verifies that the native library resolves from `pypdfium2_raw/libpdfium.so`, that the helper/build identity is exact and that the build does not enable V8/XFA. A system PDFium or source-build fallback is not accepted silently. The renderer never initializes PDF form environments and uses `may_draw_forms=False`; annotations are not drawn. Password/encrypted PDFs are rejected in v1, and malformed/truncated documents fail closed.

### `pdfium-raster-v1` contract

The persisted import identity freezes these raster choices:

- PDFium page bounding box policy (`get_bbox()`, the MediaBox/CropBox intersection exposed by PDFium), embedded page rotation and PDFium canvas units;
- fixed 200 DPI (`200 / 72` scale), no caller-selected renderer path or flags;
- white background, RGB PNG, Pillow `compress_level=6`, `optimize=False`;
- annotations/forms disabled; explicit PDFium smoothing/cache/byte-order flags;
- contiguous source order, including duplicate pages and blank pages;
- SHA-256, dimensions, byte size, bounding box, rotation and renderer provenance recorded per raster.

PDFium does not expose `/UserUnit` as a separate helper value. The v1 contract therefore treats the bounded PDFium canvas-unit result as authoritative and regression-tests unusual `UserUnit` input for deterministic output rather than applying a second guessed scale. Same supported source bytes under the same pinned runtime contract are covered by deterministic raster-byte/hash regressions.

### PDF limits and failure semantics

| Setting / invariant | v1 limit |
| --- | ---: |
| Source PDF bytes | 256 MiB |
| PDF pages | 200 |
| Raster side | 10,000 px |
| Raster pixels per Page | 25 MP |
| Aggregate raster pixels | 1,000,000,000 |
| Encoded raster bytes per Page | 12 MiB |
| Aggregate encoded raster bytes | 512 MiB |
| Per-import logical spool envelope | 768 MiB |
| Production input/control tmpfs | 272 MiB |
| Production renderer-output tmpfs | 544 MiB |
| Renderer wall time | 180 s |
| Import lease | 240 s |
| Source/orphan privacy ceiling | exactly 1 h |

Every produced PNG is opened from the untrusted output channel through the descriptor-relative/same-fd boundary, checked for regular-file/no-symlink/no-hardlink semantics and bounded size, compared with its manifest size/hash, and then passed through the same `ImageValidator` used by ordinary image upload. Manifest JSON is consumed through the same bounded descriptor path. Manifest identity, source digest, fence, page order, aggregate counts, renderer provenance, raster dimensions and hashes are checked before commit. Failures use bounded public classes such as `pdf_invalid`, `pdf_encrypted_unsupported`, `pdf_page_limit`, `pdf_geometry_limit`, `pdf_pixel_limit`, `pdf_raster_bytes_limit`, `pdf_renderer_timeout`, `pdf_renderer_crash`, `pdf_temp_storage_exhausted`, `pdf_render_failed`, `pdf_raster_validation_failed` and `pdf_manifest_invalid`.

### Atomicity, idempotency and cleanup

The original PDF and intermediate rasters are transient spool material, not library objects. The coordinator owns database/storage credentials, lease/fencing state and the input/control topology. The renderer can only read coordinator-published inputs and can write only inside designated untrusted output leaves. The privileged importer consumes those outputs through the descriptor-relative boundary described above. A source is deleted immediately after a terminal success/failure when cleanup succeeds, while queued/rendering sources have a fixed one-hour ceiling and expired/orphaned imports are reconciled. Normal Document/Page retention remains exactly 24 hours once the logical Document exists.

The PDF request digest binds source-PDF SHA-256, requested study language, source kind `pdf` and raster-contract version. Filenames are not content identity. Replaying the same key/request recovers the original import/result; the same key with materially different content or language conflicts. Lease recovery increments a fencing token, and stale renderer manifests cannot commit even if old output remains present.

Atomic commit happens only after all rasters validate. One PostgreSQL transaction creates one `Document(source_kind=pdf)`, all ordered ordinary Pages and all normal initial Page jobs. Identical raster bytes can share the existing immutable blob, while duplicate source pages remain distinct ordered Page memberships. If render, manifest, raster validation, fencing, timeout or resource checks fail, zero logical Document Pages are committed.

### Creation idempotency

The plaintext `Idempotency-Key` is never persisted. The persisted identity uses the existing peppered HMAC convention and a request digest that binds:

- source kind (`images` for direct multi-image creation, `pdf` for transient PDF import);
- requested study language;
- ordered image-content SHA-256 digests for direct-image creation, including duplicate positions, or source PDF SHA-256 plus raster-contract version for PDF import.

Filenames are not part of request identity.

The same key with the same material request replays the same logical operation without creating duplicate Documents, Pages, or jobs. Reusing the key with materially different content, order, count, language or PDF contract returns an idempotency conflict.

## Limits

Per-image validation remains unchanged:

- JPEG, PNG, or WebP only;
- maximum 12 MiB per image;
- maximum 10,000 pixels on either side;
- maximum 25 megapixels per image.

Document creation adds configurable aggregate limits:

| Setting | Default | Purpose |
| --- | ---: | --- |
| `MANGASENSEI_MAX_DOCUMENT_IMAGES` | `200` | Bounds page memberships and initial jobs near the intended manga-volume scale. |
| `MANGASENSEI_MAX_DOCUMENT_BYTES` | `536870912` (512 MiB) | Prevents the 200-page worst case from reaching 2.4 GiB even though each image retains its own 12 MiB limit. |
| `MANGASENSEI_MAX_DOCUMENT_PIXELS` | `1000000000` (1 billion) | Bounds aggregate decode/storage pressure while preserving the existing per-image 25 MP defense. |

The API rejects an aggregate before creating a visible Document. Image validation remains authoritative for media type, dimensions, decoded pixels, and immutable storage metadata.

## Transaction and blob storage boundary

Document membership and all initial jobs are created in one PostgreSQL transaction. Product-visible success therefore means exactly one Document, exactly N ordered child Pages, and exactly N initial `page_analysis` jobs.

Filesystem blob publication and PostgreSQL commit are different durability boundaries. Document upload reuses the existing digest-locked content-addressed staging primitives:

1. acquire unique image-content locks in deterministic digest order;
2. stage/deduplicate immutable blobs;
3. create Document, child Pages, and initial jobs in the database transaction;
4. commit the database transaction;
5. confirm staged filesystem markers.

A failed transaction cannot expose a half-created Document. Staged markers allow later reconciliation when marker cleanup fails after a successful commit. Shared live blobs are never deleted merely because another document creation fails or expires.

## Retention

A newly created Document and every child Page share the exact same `created_at` and `expires_at` values. The lifetime is exactly 24 hours.

Reading, navigation, retry, cancellation, reorder, study-language reprocessing, and English dictionary reprojection do not extend retention. Existing retention cleanup removes expired Documents/Pages while preserving an immutable blob that is still referenced by another live Page. A `cancelled` job may later transition to `expired` as part of ordinary retention.

## Document capabilities

Creation returns four document-scoped capabilities:

- `read:document` for aggregate metadata/progress and nested StudyPage reads;
- `read:document-image` for nested original-image reads;
- `reprocess:document` for nested current-child language reprocessing;
- `manage:document` for failed-page retry, Document cancellation, and post-create reorder.

Tokens are sent only through `X-Document-Token` headers. Capability tokens are never stored in URLs or query parameters, and only their HMAC digests are persisted. Each capability expires exactly with its Document.

A resource UUID alone is not authorization. Wrong tokens, wrong scopes, wrong Documents, non-member Pages, expired/revoked capabilities, and missing resources all use the same not-found behavior.

The current SPA keeps document capability tokens only in the active in-memory page session. It does not put them into browser history, query parameters, or `localStorage`. Reload therefore loses the active Document access instead of weakening capability secrecy. Durable secret-safe session restoration is deferred until there is a broader session design.

## Read and progress contract

`GET /api/v1/documents/{documentId}` returns the ordered page summaries plus the aggregate status and progress projection. `GET /api/v1/documents/{documentId}/progress` returns the same counters.

Each summary contains the public Page UUID, zero-based ordinal, latest job status, and `resultAvailable`. Aggregate counters are mutually exclusive:

```text
completedPages + processingPages + failedPages + cancelledPages == totalPages
```

The aggregate Document status is derived from child state:

- any active child work -> `processing`;
- no active work plus any terminal cancelled unreadable child -> `cancelled`;
- no active work plus any terminal failed/expired unreadable child -> `completed_with_errors`;
- otherwise -> `completed`.

A Page with a successful persisted StudyResult remains `resultAvailable=true` while a later reprocess job is active or after that later job fails or is cancelled. The reader uses `resultAvailable`, not only the newest job status, to decide whether a child can be opened, so an older successful StudyResult remains readable.

The SPA uses one bounded Document-level polling loop. It does not create one timer or one StudyPage request loop per child. Polling is driven by the aggregate `processing` state and stops when the Document reaches a terminal aggregate state. No OCR percentage or ETA is fabricated.

## Reader navigation and partial results

The document reader owns aggregate access, ordered page summaries, current page selection, the selected `StudyPage`, and one authenticated Blob URL.

Navigation provides:

- current page / total count;
- Previous and Next controls;
- a compact page/status index suitable for roughly 200 pages;
- readable, processing, failed, and cancelled status presentation.

No thumbnail pipeline is created and the browser does not download every original image to populate navigation.

A completed child can be opened while siblings are still processing or later fail/cancel. Selecting an incomplete child keeps the document shell and progress visible without pretending a StudyPage exists. When the selected child becomes readable, the reader fetches only its nested StudyPage and protected original image.

Changing pages aborts obsolete client requests, revokes the previous Blob URL, and uses a request-generation guard so a late response from Page A cannot replace Page B. A browser abort is never interpreted as backend job cancellation.

## Language behavior

Content remains Japanese. UI locale (`en`/`pt-BR`) and study language (`pt-BR`/`en`) remain independent. The deterministic local dictionary is English-only; furigana and page-fit/zoom preferences remain browser-local presentation choices across page navigation.

Persisted results remain page-scoped authority. A preference does not bulk-recompute the whole Document:

- changing study language while viewing Page 7 uses the nested Document reprocess route for Page 7 only;
- the reader exposes no dictionary-language selector and does not initiate non-English dictionary reprojection;
- an obsolete browser dictionary preference such as `de` normalizes to English rather than reactivating retired pack behavior.

The protected API still permits an English dictionary-only reprojection. That path delegates the existing dictionary projection job, reuses the persisted canonical linguistic result, and performs zero OCR reruns, zero new `LinguisticRun`, zero Sudachi lexical acquisition, and zero Gemini calls. New non-English dictionary requests are rejected before a projection job/request is created.

Historical completed results may still contain older requested/effective/fallback dictionary-language and source metadata. Those persisted fields remain readable for upgrade safety and do not require downloading or loading the retired German pack. Japanese source text retains `lang="ja"`; historical meaning language annotations reflect the persisted effective language rather than rewriting old data.

## Nested reprocess route

`POST /api/v1/documents/{documentId}/pages/{pageId}/reprocess` requires `reprocess:document`, verifies Document membership, then delegates the existing Page reprocess service.

The JSON request accepts exactly one supported axis. Study-language reprocessing uses:

```json
{"studyLanguage":"en"}
```

The retained dictionary projection API path accepts English only:

```json
{"dictionaryLanguage":"en"}
```

Values such as `de` or `pt-BR` are unsupported for new dictionary requests and fail the normal validation contract; they are not silently normalized to English. Supplying both study and dictionary axes is also invalid.

The Page one-active-job invariant, idempotency semantics, worker leases/fencing, study-language analysis, and English dictionary-only projection behavior are unchanged.

## Retry failed pages

`POST /api/v1/documents/{documentId}/retry-failed` requires `manage:document` and an `Idempotency-Key` header.

The operation is bounded by the current Document page limit and records a durable retry-request ledger. It selects only unreadable child Pages whose latest job is terminal `failed`. Children that are active, `retryable_failure`, already readable, `cancelled`, or `expired` are not duplicated.

Replaying the same idempotency key for the same Document returns the original retry batch/job identities rather than creating another batch.

## Cancellation

`POST /api/v1/documents/{documentId}/cancel` requires `manage:document`.

Cancellation is cooperative and page/job aware:

- pending or unleased retryable work may transition to `cancelled` immediately;
- leased/processing work records durable cancel intent first;
- the lease owner acknowledges cancellation at safe fenced checkpoints;
- lease recovery terminalizes abandoned cancel-requested work as `cancelled`;
- already completed or failed children are not rewritten.

Cancellation does not hard-preempt arbitrary computation already in progress. Fencing and lease ownership remain authoritative, so post-cancel persistence cannot bypass the normal ownership checks.

## Post-create reorder

`PUT /api/v1/documents/{documentId}/order` requires `manage:document`.

The JSON request supplies the complete ordered Page membership plus `expectedOrderRevision`. A stale revision returns a conflict. Missing, duplicate, or non-member Page IDs are rejected.

A successful reorder rewrites only contiguous Page ordinals. Page identity, Job/StudyResult identity, original images, and expiry remain unchanged. `orderRevision` increments only when the requested order actually differs from the persisted order.

## Deferred work

The current Document contract intentionally does not implement:

- thumbnails;
- two-page spreads or cross-page reading order;
- a persistent manga library;
- later large-document/performance hardening beyond the current bounded limits.

Slice E remains deferred under #105; Slice D does not add thumbnails, a persistent manga library, spread-aware/cross-page reading order, or later large-document/performance work beyond the bounded PDF contract above.
