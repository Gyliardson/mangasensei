# Multi-image document imports

MangaSensei supports an ordered set of JPEG, PNG, or WebP images as one temporary `Document`. PDF import is not implemented in this slice.

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

### Creation idempotency

The plaintext `Idempotency-Key` is never persisted. The persisted identity uses the existing peppered HMAC convention and a request digest that binds:

- source kind (`images`);
- requested study language;
- page count;
- ordered image-content SHA-256 digests, including duplicate positions.

Filenames are not part of request identity.

The same key with the same ordered content and study language replays the same logical Document and reissues fresh valid document capability tokens without creating duplicate Documents, Pages, or jobs. Reusing the key with different content, order, count, or study language returns an idempotency conflict.

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

Reading, navigation, retry, cancellation, reorder, study-language reprocessing, and dictionary-only reprojection do not extend retention. Existing retention cleanup removes expired Documents/Pages while preserving an immutable blob that is still referenced by another live Page. A `cancelled` job may later transition to `expired` as part of ordinary retention.

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

UI locale (`en`/`pt-BR`), study language (`pt-BR`/`en`), requested dictionary language (`en`/`de`/`pt-BR`), furigana, and page-fit/zoom preferences remain independent browser preferences across page navigation.

Persisted results remain page-scoped authority. A preference does not bulk-recompute the whole Document:

- changing study language while viewing Page 7 uses the nested Document reprocess route for Page 7 only;
- changing dictionary language while viewing Page 7 runs dictionary-only reprojection for Page 7 only;
- opening a completed child whose persisted dictionary request differs from the browser preference lazily reprojects only that child.

Dictionary-only reprojection delegates the existing dictionary projection job. It reuses the persisted canonical linguistic result and performs zero OCR reruns, zero new `LinguisticRun`, zero Sudachi lexical acquisition, and zero Gemini calls. The last completed result remains readable while reprojection runs.

The existing dictionary fallback contract is unchanged: German uses reviewed local JMdict projections when available; `pt-BR` remains the requested language but currently uses explicit English deterministic fallback; Japanese source text retains `lang="ja"`, German meanings `lang="de"`, and English fallback `lang="en"`.

## Nested reprocess route

`POST /api/v1/documents/{documentId}/pages/{pageId}/reprocess` requires `reprocess:document`, verifies Document membership, then delegates the existing Page reprocess service.

The JSON request accepts exactly one axis:

```json
{"studyLanguage":"en"}
```

or:

```json
{"dictionaryLanguage":"de"}
```

The Page one-active-job invariant, idempotency semantics, worker leases/fencing, study-language analysis, and dictionary-only projection behavior are unchanged.

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

- PDF import or a hardened local PDF renderer;
- thumbnails;
- two-page spreads or cross-page reading order;
- a persistent manga library;
- later large-document/performance hardening beyond the current bounded limits.

PDF support remains deferred to a later slice of #105.
