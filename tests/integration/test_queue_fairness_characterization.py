from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mangasensei.infrastructure.database.document_models import DocumentRecord
from mangasensei.infrastructure.database.job_models import JobAttemptRecord, JobRecord
from mangasensei.infrastructure.database.queue_repository import QueueRepository
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord
from tests.integration.test_queue import async_url


async def _seed_page_job(
    sessions: async_sessionmaker[AsyncSession],
    *,
    seed: bytes,
    available_at: datetime,
    document_id: int | None,
    ordinal: int | None,
) -> int:
    digest = hashlib.sha256(seed).digest()
    async with sessions.begin() as session:
        blob = ImageBlobRecord(
            sha256=digest,
            byte_size=100,
            width=10,
            height=10,
            media_type="image/png",
            storage_key=f"objects/{digest.hex()[:2]}/{digest.hex()[2:4]}/{digest.hex()}",
        )
        session.add(blob)
        await session.flush()
        page = PageRecord(
            image_blob_id=blob.id,
            document_id=document_id,
            ordinal=ordinal,
            original_filename=f"{seed.decode('ascii')}.png",
            upload_key_id=None if document_id is not None else "v1",
            upload_idempotency_digest=(
                None
                if document_id is not None
                else hashlib.sha256(seed + b"-upload").digest()
            ),
            request_digest=digest,
        )
        session.add(page)
        await session.flush()
        job = JobRecord(
            page_id=page.id,
            idempotency_digest=hashlib.sha256(seed + b"-job").digest(),
            request_digest=digest,
            available_at=available_at,
        )
        session.add(job)
        await session.flush()
        return job.id


async def _seed_document(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions.begin() as session:
        document = DocumentRecord(source_kind="images")
        session.add(document)
        await session.flush()
        return document.id


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_queue_large_document_fairness_characterization(
    clean_postgres_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = create_async_engine(async_url(clean_postgres_url))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    base = datetime.now(UTC)
    ownership: dict[int, str] = {}

    document_a = await _seed_document(sessions)
    for ordinal in range(200):
        job_id = await _seed_page_job(
            sessions,
            seed=f"fairness-a-{ordinal:03d}".encode(),
            available_at=base - timedelta(seconds=4),
            document_id=document_a,
            ordinal=ordinal,
        )
        ownership[job_id] = "document-a"

    standalone_job = await _seed_page_job(
        sessions,
        seed=b"fairness-standalone",
        available_at=base - timedelta(seconds=3),
        document_id=None,
        ordinal=None,
    )
    ownership[standalone_job] = "standalone"

    small_document = await _seed_document(sessions)
    for ordinal in range(3):
        job_id = await _seed_page_job(
            sessions,
            seed=f"fairness-small-{ordinal}".encode(),
            available_at=base - timedelta(seconds=2),
            document_id=small_document,
            ordinal=ordinal,
        )
        ownership[job_id] = "small-document"

    retry_job = await _seed_page_job(
        sessions,
        seed=b"fairness-recovered",
        available_at=base - timedelta(seconds=1),
        document_id=None,
        ordinal=None,
    )
    ownership[retry_job] = "recovered-retry"
    claimed_at = base - timedelta(seconds=2)
    async with sessions.begin() as session:
        await session.execute(
            update(JobRecord)
            .where(JobRecord.id == retry_job)
            .values(
                status="claimed",
                worker_id="fairness-expired-worker",
                attempt_count=1,
                fencing_token=1,
                claimed_at=claimed_at,
                heartbeat_at=claimed_at,
                lease_expires_at=base - timedelta(seconds=1),
                started_at=claimed_at,
            )
        )
        session.add(
            JobAttemptRecord(
                job_id=retry_job,
                attempt_no=1,
                fencing_token=1,
                worker_id="fairness-expired-worker",
                claimed_at=claimed_at,
                heartbeat_at=claimed_at,
                lease_expires_at=base - timedelta(seconds=1),
            )
        )

    repository = QueueRepository(sessions)
    assert await repository.recover_expired_leases() == 1

    claims: list[dict[str, object]] = []
    for claim_order in range(1, len(ownership) + 1):
        claim = await repository.claim(
            worker_id=f"fairness-probe-{claim_order:03d}", lease_seconds=60
        )
        assert claim is not None
        claims.append(
            {
                "claimOrder": claim_order,
                "jobId": claim.job_id,
                "pageId": claim.page_id,
                "aggregate": ownership[claim.job_id],
                "attemptNo": claim.attempt_no,
                "fencingToken": claim.fencing_token,
            }
        )
    assert await repository.claim(worker_id="fairness-probe-end", lease_seconds=60) is None
    assert len({claim["jobId"] for claim in claims}) == len(ownership)

    def first_index(owner: str) -> int:
        return next(
            index for index, claim in enumerate(claims) if claim["aggregate"] == owner
        )

    artifact = {
        "schemaVersion": 1,
        "purpose": "characterization-only; not a fairness policy contract",
        "workload": {
            "documentAPageJobs": 200,
            "laterStandalonePages": 1,
            "laterSmallDocumentPageJobs": 3,
            "laterRecoveredRetryJobs": 1,
        },
        "summary": {
            "claimsFromDocumentABeforeStandalone": first_index("standalone"),
            "claimsBeforeSmallDocumentFirstReceivesWork": first_index("small-document"),
            "claimsBeforeRecoveredRetryReceivesWork": first_index("recovered-retry"),
            "totalClaims": len(claims),
        },
        "claims": claims,
    }
    output = os.environ.get("MANGASENSEI_QUEUE_CHARACTERIZATION_OUTPUT")
    if output:
        path = Path(output)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        payload = json.dumps(artifact, indent=2, sort_keys=True) + "\n"
        await asyncio.to_thread(path.write_text, payload, encoding="utf-8")
    with capsys.disabled():
        print(
            "large_document_queue_characterization "
            + json.dumps(artifact["summary"], sort_keys=True)
        )
    await engine.dispose()