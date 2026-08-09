"""Durable reconciliation for abandoned Gemini budget reservations."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mangasensei.infrastructure.database.analysis_models import (
    GeminiBudgetBucketRecord,
    GeminiCallRecord,
    GeminiCostLedgerRecord,
)


async def reconcile_abandoned_gemini_calls(
    session: AsyncSession,
    *,
    job_id: int | None = None,
    fencing_token: int | None = None,
    page_ids: tuple[int, ...] = (),
) -> int:
    """Settle open calls for one fenced attempt or a retention page batch.

    A reserved-but-unsent call releases its reservation and is detached from the
    page so it does not consume the page's bounded external-call ordinal. A sent
    call is conservatively charged at the reservation upper bound and becomes
    ``unknown``. Only ``reserved``/``sent`` rows are selected, making repeated
    reconciliation idempotent.
    """

    attempt_selector = job_id is not None or fencing_token is not None
    page_selector = bool(page_ids)
    if attempt_selector == page_selector:
        raise ValueError("select exactly one fenced attempt or one page batch")
    if attempt_selector and (job_id is None or fencing_token is None):
        raise ValueError("job_id and fencing_token must be supplied together")

    predicates = [GeminiCallRecord.state.in_(("reserved", "sent"))]
    if attempt_selector:
        predicates.extend(
            (
                GeminiCallRecord.job_id == job_id,
                GeminiCallRecord.fencing_token == fencing_token,
            )
        )
    else:
        predicates.append(GeminiCallRecord.page_id.in_(page_ids))

    calls = (
        await session.execute(
            select(GeminiCallRecord).where(*predicates).order_by(GeminiCallRecord.id).with_for_update()
        )
    ).scalars()

    reconciled = 0
    for call in calls:
        bucket = (
            await session.execute(
                select(GeminiBudgetBucketRecord)
                .where(
                    GeminiBudgetBucketRecord.budget_date == call.created_at.date(),
                    GeminiBudgetBucketRecord.currency == "USD",
                )
                .with_for_update()
            )
        ).scalar_one()
        bucket.reserved_amount = max(Decimal("0"), bucket.reserved_amount - call.reserved_cost)
        if call.state == "sent":
            bucket.actual_amount += call.reserved_cost
            call.state = "unknown"
            session.add(
                GeminiCostLedgerRecord(
                    gemini_call_id=call.id,
                    observation_key="uncertain-request-upper-bound-v1",
                    pricing_version="reservation-upper-bound-v1",
                    usage_category="unknown_upper_bound",
                    token_quantity=1,
                    unit_rate=call.reserved_cost,
                    amount=call.reserved_cost,
                )
            )
        else:
            call.state = "failed"
            # A reservation that never reached the provider is not an external
            # page call. Detaching it lets a later attempt reuse that ordinal
            # while retaining job/fencing provenance for the failed record.
            call.page_id = None
        call.finished_at = func.now()
        reconciled += 1
    return reconciled
