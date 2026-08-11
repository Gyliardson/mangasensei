# Document recovery controls

This document describes the recovery and aggregate-control contract added in issue #105 Slice C.
It does not describe PDF import or large-document hardening; those remain deferred work in #105.

## Processing boundary

`Page` remains the atomic processing, retry, result, and retention unit inside an ordered `Document`.
A Document does not create a monolithic OCR job. Completed sibling pages remain readable when another
page fails, is retried, or is cancelled.

The Document aggregate status is a projection of durable child state:

- `processing`: at least one child has unresolved latest work (`pending`, claimed/processing stages, or
  `retryable_failure`), including a later reprocess of a page that already has a readable result;
- `completed`: every child has a readable successful result and no unresolved work or unreadable
  terminal warning state remains;
- `completed_with_errors`: processing is terminal and at least one child has no readable result because
  its latest job is terminally failed or expired;
- `cancelled`: processing is terminal and explicit Document cancellation left at least one child
  cancelled without a readable result.

A successful result is not invalidated by a later reprocess failure or cancellation. The page remains
readable, while active later reprocessing still keeps the aggregate in `processing` until that work is
terminal.

Progress counters partition current Document membership into readable, processing, failed, and
cancelled pages. They are counts, not a fabricated percentage.

## Management capability

Creation returns a separate `manage:document` capability in addition to the existing Document read,
image-read, and reprocess capabilities. Management authorization is required for bulk retry,
cancellation, and persisted reorder. Read or reprocess capability possession does not implicitly grant
management authority.

As with other capabilities, the token is opaque, sent only in the `X-Document-Token` header, and only a
digest is persisted. Resource identifiers are not authorization. Unauthorized management requests use
the same not-found behavior as other protected Document resources.

All Document capabilities retain the existing Document expiry; management operations do not extend
retention.

## Retry failed pages

`POST /api/v1/documents/{document_id}/retry-failed` requires `manage:document` and an
`Idempotency-Key` header.

The operation is bounded by the configured Document page limit and creates independent Page jobs only
for currently eligible children:

- the latest Page job is exactly terminal `failed`;
- no successful StudyResult already makes that Page readable;
- no active work is duplicated;
- `retryable_failure` is excluded because queue recovery still owns that job;
- successful, cancelled, and expired children are not recomputed by this bulk operation.

A durable `document_retry_requests` ledger binds the idempotency digest to the retry batch. Replaying
the same key returns the same batch membership/job identifiers and does not sweep in a failure that
occurred later. A different key issued while the retry job is active creates no duplicate active work.
Existing Page/Job history remains auditable.

## Cancellation and fencing

`POST /api/v1/documents/{document_id}/cancel` requires `manage:document` and is idempotent.
Cancellation is a durable queue operation, not the reader's client-side decision to stop polling.

Pending and unleased `retryable_failure` jobs can be terminalized as `cancelled` immediately. Claimed or
processing jobs are not rewritten by the API actor: the API records `cancel_requested_at` and leaves the
lease owner and fencing token intact.

The worker checks cancellation at safe stage boundaries. The current lease owner can acknowledge the
intent only while its worker identity, fencing token, expected stage, and unexpired lease still match.
Acknowledgement clears lease state, closes the attempt as cancelled, reconciles any abandoned Gemini
reservation, and stores no source/OCR text in cancellation diagnostics.

Normal worker persistence predicates exclude cancel-requested jobs, so a stale or non-owner worker
cannot commit a later processing transition after cancellation intent wins the row race. Expired lease
recovery terminalizes cancel-requested work as `cancelled` instead of requeueing it. Completed and
terminal-failed historical jobs are never rewritten by Document cancellation.

## Persisted reorder

`PUT /api/v1/documents/{document_id}/order` requires `manage:document` and accepts the complete ordered
Page membership plus the caller's expected `orderRevision`.

The requested set must contain every current member exactly once. Duplicate, missing, or non-member
Page identifiers are rejected. A stale revision fails with a conflict. Reorder is atomic and assigns
contiguous ordinals while preserving Page identifiers, Jobs, StudyResults, image storage, and expiry.
A no-op reorder does not increment the revision.

Reader navigation uses the persisted order returned by the Document snapshot, so a fresh server read
reflects the new sequence.

## Reader controls

The reader exposes terminal `completed_with_errors` and `cancelled` states, identifies failed/cancelled
children, keeps readable siblings navigable, and provides:

- **Retry failed pages**;
- **Cancel processing**;
- move-current-page earlier/later controls backed by persisted reorder.

Document mutation requests have their own abort/state handling. Existing selected-page request
generation guards remain in place so a late response from a previously selected child cannot replace
the current child. The controls are responsive and labelled for keyboard/screen-reader use.

## Deferred #105 work

Slice C intentionally does not add PDF import/rendering or the broader large-document/performance
hardening still tracked by #105. The issue remains open for those later slices.
