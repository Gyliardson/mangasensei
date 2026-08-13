from __future__ import annotations

import asyncio
import errno
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pypdfium2 as pdfium
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import mangasensei.application.pdf_imports as pdf_imports_application
from mangasensei.api.app import create_app
from mangasensei.application.pdf_imports import PdfImportCoordinator, _ClaimedImport
from mangasensei.config import Settings
from mangasensei.infrastructure.database.document_import_models import DocumentImportRecord
from mangasensei.infrastructure.database.document_models import DocumentRecord
from mangasensei.infrastructure.database.job_models import JobRecord
from mangasensei.infrastructure.database.session import create_database
from mangasensei.infrastructure.database.storage_models import PageRecord
from mangasensei.pdf_imports.contracts import PdfRasterManifest
from mangasensei.pdf_imports.renderer import PdfRenderer
from mangasensei.pdf_imports.spool import PdfSpool
from mangasensei.storage.images import ImageValidator, ValidatedImage
from mangasensei.storage.local import LocalFilesystemStorage

_PEPPER = "pdf-review-fix-integration-pepper-0001"


def _settings(database_url: str, root: Path) -> Settings:
    return Settings(
        environment="test",
        database_url=database_url.replace("postgresql://", "postgresql+psycopg://", 1),
        storage_root=root / "storage",
        pdf_spool_root=root / "pdf-spool",
        model_cache=root / "models",
        capability_peppers=(_PEPPER,),
    )


def _pdf_bytes() -> bytes:
    path = Path.cwd() / ".pytest-pdf-review-fix.pdf"
    document = pdfium.PdfDocument.new()
    try:
        page = document.new_page(72, 72)
        page.close()
        document.save(path)
    finally:
        document.close()
    try:
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


async def _run_actual_render_boundary(
    settings: Settings,
    coordinator: PdfImportCoordinator,
) -> None:
    task = asyncio.create_task(coordinator.run_once())
    request_dir = settings.pdf_spool_root / "requests"
    for _ in range(200):
        if request_dir.exists() and any(request_dir.glob("*.request.json")):
            break
        await asyncio.sleep(0.01)
    else:
        task.cancel()
        raise AssertionError("coordinator did not publish a renderer request")

    await asyncio.to_thread(PdfRenderer(settings).run_once)
    assert await task is True


def _coordinator(
    settings: Settings,
    sessions: async_sessionmaker[AsyncSession],
    *,
    worker_id: str,
    spool: PdfSpool | None = None,
) -> PdfImportCoordinator:
    return PdfImportCoordinator(
        sessions=sessions,
        storage=LocalFilesystemStorage(settings.storage_root),
        spool=spool or PdfSpool(settings.pdf_spool_root),
        image_validator=ImageValidator(
            max_bytes=settings.max_upload_bytes,
            max_pixels=settings.max_image_pixels,
            max_side=settings.max_image_side,
        ),
        settings=settings,
        idempotency_pepper=_PEPPER,
        worker_id=worker_id,
    )


async def _admit_pdf(settings: Settings, *, idempotency_key: str) -> UUID:
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        accepted = await client.post(
            "/api/v1/document-imports",
            headers={"Idempotency-Key": idempotency_key},
            files={"pdf": ("lease.pdf", _pdf_bytes(), "application/pdf")},
        )
    assert accepted.status_code == 202
    return UUID(accepted.json()["data"]["importId"])


async def _expire_claim(
    sessions: async_sessionmaker[AsyncSession], claim: _ClaimedImport
) -> None:
    async with sessions.begin() as session:
        record = (
            await session.execute(
                select(DocumentImportRecord)
                .where(DocumentImportRecord.id == claim.internal_id)
                .with_for_update()
            )
        ).scalar_one()
        assert record.status == "rendering"
        assert record.fencing_token == claim.fencing_token
        record.lease_until = datetime.now(UTC) - timedelta(seconds=1)


class _ExpireImmediatelyBeforeCommit(PdfImportCoordinator):
    async def _commit_document(
        self,
        claim: _ClaimedImport,
        manifest: PdfRasterManifest,
        images: tuple[ValidatedImage, ...],
    ) -> None:
        await _expire_claim(self._sessions, claim)
        await super()._commit_document(claim, manifest, images)


class _ExpireAndReclaimImmediatelyBeforeCommit(PdfImportCoordinator):
    _recovery: PdfImportCoordinator
    recovery_claim: _ClaimedImport | None = None
    winner_attempt: Path | None = None

    async def _commit_document(
        self,
        claim: _ClaimedImport,
        manifest: PdfRasterManifest,
        images: tuple[ValidatedImage, ...],
    ) -> None:
        await _expire_claim(self._sessions, claim)
        recovery_claim = await self._recovery._claim()
        assert recovery_claim is not None
        assert recovery_claim.fencing_token == claim.fencing_token + 1
        self.recovery_claim = recovery_claim
        self.winner_attempt = self._spool.prepare_attempt_dir(
            recovery_claim.public_id, recovery_claim.fencing_token
        )
        await super()._commit_document(claim, manifest, images)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_pdf_import_lease_cannot_commit_document(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    settings = _settings(clean_postgres_url, tmp_path)
    await _admit_pdf(settings, idempotency_key="expired-commit-001")
    engine, sessions = create_database(settings.require_database_url())
    coordinator = _coordinator(settings, sessions, worker_id="expired-owner")
    expiring = _ExpireImmediatelyBeforeCommit(
        sessions=sessions,
        storage=coordinator._storage,
        spool=coordinator._spool,
        image_validator=coordinator._validator,
        settings=settings,
        idempotency_pepper=_PEPPER,
        worker_id="expired-owner",
    )
    try:
        await _run_actual_render_boundary(settings, expiring)
        async with sessions() as session:
            assert await session.scalar(select(func.count(DocumentRecord.id))) == 0
            assert await session.scalar(select(func.count(PageRecord.id))) == 0
            assert await session.scalar(select(func.count(JobRecord.id))) == 0
            record = (await session.execute(select(DocumentImportRecord))).scalar_one()
            assert record.status == "rendering"
            assert record.fencing_token == 1
            assert record.lease_owner == "expired-owner"
            assert record.lease_until is not None
            assert record.lease_until < datetime.now(UTC)
            assert record.document_id is None
            assert record.finished_at is None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pdf_import_lease_loss_during_document_staging_rolls_back_commit(
    clean_postgres_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(clean_postgres_url, tmp_path)
    import_id = await _admit_pdf(settings, idempotency_key="mid-staging-expiry-001")
    engine, sessions = create_database(settings.require_database_url())
    spool = PdfSpool(settings.pdf_spool_root)
    coordinator = _coordinator(settings, sessions, worker_id="stale-owner", spool=spool)
    recovery = _coordinator(settings, sessions, worker_id="winner", spool=spool)
    winner_attempt = spool.prepare_attempt_dir(import_id, 2)
    original_stage_image_blob = pdf_imports_application.stage_image_blob
    real_datetime = datetime
    lease_deadline: datetime | None = None
    clock_advanced = False
    staging_hook_reached = False

    class _CommitClock(datetime):
        @classmethod
        def now(cls, tz=None):
            if not clock_advanced:
                return real_datetime.now(tz)
            assert lease_deadline is not None
            advanced = lease_deadline + timedelta(seconds=1)
            if tz is None:
                return advanced.replace(tzinfo=None)
            return advanced.astimezone(tz)

    async def advance_clock_after_real_blob_staging(
        session: AsyncSession,
        *,
        storage: LocalFilesystemStorage,
        image: ValidatedImage,
    ):
        nonlocal clock_advanced, lease_deadline, staging_hook_reached
        staged = await original_stage_image_blob(session, storage=storage, image=image)
        record = (await session.execute(select(DocumentImportRecord))).scalar_one()
        now = real_datetime.now(UTC)
        assert record.status == "rendering"
        assert record.fencing_token == 1
        assert record.lease_owner == "stale-owner"
        assert record.lease_until is not None
        assert record.lease_until > now
        assert await session.scalar(select(func.count(DocumentRecord.id))) == 1
        lease_deadline = record.lease_until
        clock_advanced = True
        staging_hook_reached = True
        return staged

    monkeypatch.setattr(pdf_imports_application, "datetime", _CommitClock)
    monkeypatch.setattr(
        pdf_imports_application,
        "stage_image_blob",
        advance_clock_after_real_blob_staging,
    )
    try:
        await _run_actual_render_boundary(settings, coordinator)
        assert staging_hook_reached
        assert clock_advanced
        assert lease_deadline is not None
        assert lease_deadline < _CommitClock.now(UTC)

        async with sessions() as session:
            assert await session.scalar(select(func.count(DocumentRecord.id))) == 0
            assert await session.scalar(select(func.count(PageRecord.id))) == 0
            assert await session.scalar(select(func.count(JobRecord.id))) == 0
            record = (await session.execute(select(DocumentImportRecord))).scalar_one()
            assert record.public_id == import_id
            assert record.status == "rendering"
            assert record.fencing_token == 1
            assert record.lease_owner == "stale-owner"
            assert record.lease_until == lease_deadline
            assert record.document_id is None
            assert record.finished_at is None

        assert not spool.attempt_dir(import_id, 1).exists()
        assert winner_attempt.is_dir()

        recovery_claim = await recovery._claim()
        assert recovery_claim is not None
        assert recovery_claim.public_id == import_id
        assert recovery_claim.fencing_token == 2
        assert winner_attempt.is_dir()
        async with sessions() as session:
            record = (await session.execute(select(DocumentImportRecord))).scalar_one()
            assert record.status == "rendering"
            assert record.fencing_token == 2
            assert record.lease_owner == "winner"
            assert record.document_id is None
            assert record.finished_at is None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_pdf_import_lease_cannot_terminal_fail(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    settings = _settings(clean_postgres_url, tmp_path)
    await _admit_pdf(settings, idempotency_key="expired-failure-001")
    engine, sessions = create_database(settings.require_database_url())
    coordinator = _coordinator(settings, sessions, worker_id="expired-owner")
    try:
        claim = await coordinator._claim()
        assert claim is not None
        await _expire_claim(sessions, claim)
        await coordinator._terminal_failure(claim, "pdf_invalid")
        async with sessions() as session:
            record = (await session.execute(select(DocumentImportRecord))).scalar_one()
            assert record.status == "rendering"
            assert record.fencing_token == claim.fencing_token
            assert record.lease_owner == "expired-owner"
            assert record.error_code is None
            assert record.finished_at is None
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_expired_pdf_import_reclaim_fences_old_output_from_commit(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    settings = _settings(clean_postgres_url, tmp_path)
    await _admit_pdf(settings, idempotency_key="expired-reclaim-001")
    engine, sessions = create_database(settings.require_database_url())
    spool = PdfSpool(settings.pdf_spool_root)
    recovery = _coordinator(settings, sessions, worker_id="winner", spool=spool)
    coordinator = _ExpireAndReclaimImmediatelyBeforeCommit(
        sessions=sessions,
        storage=LocalFilesystemStorage(settings.storage_root),
        spool=spool,
        image_validator=ImageValidator(
            max_bytes=settings.max_upload_bytes,
            max_pixels=settings.max_image_pixels,
            max_side=settings.max_image_side,
        ),
        settings=settings,
        idempotency_pepper=_PEPPER,
        worker_id="expired-owner",
    )
    coordinator._recovery = recovery
    try:
        await _run_actual_render_boundary(settings, coordinator)
        assert coordinator.recovery_claim is not None
        assert coordinator.winner_attempt is not None
        async with sessions() as session:
            assert await session.scalar(select(func.count(DocumentRecord.id))) == 0
            assert await session.scalar(select(func.count(PageRecord.id))) == 0
            assert await session.scalar(select(func.count(JobRecord.id))) == 0
            record = (await session.execute(select(DocumentImportRecord))).scalar_one()
            assert record.status == "rendering"
            assert record.fencing_token == 2
            assert record.lease_owner == "winner"
            assert record.document_id is None
        assert not spool.attempt_dir(coordinator.recovery_claim.public_id, 1).exists()
        assert coordinator.winner_attempt.is_dir()
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pdf_import_lease_renewal_is_fenced_and_cannot_revive_expiry(
    clean_postgres_url: str,
    tmp_path: Path,
) -> None:
    settings = _settings(clean_postgres_url, tmp_path)
    await _admit_pdf(settings, idempotency_key="lease-renewal-001")
    engine, sessions = create_database(settings.require_database_url())
    owner = _coordinator(settings, sessions, worker_id="owner")
    wrong_owner = _coordinator(settings, sessions, worker_id="other")
    try:
        claim = await owner._claim()
        assert claim is not None
        async with sessions() as session:
            before = (await session.execute(select(DocumentImportRecord))).scalar_one().lease_until
        assert before is not None
        assert await owner._renew_lease(claim) is True
        async with sessions() as session:
            renewed = (await session.execute(select(DocumentImportRecord))).scalar_one().lease_until
        assert renewed is not None
        assert renewed > before

        wrong_fence = _ClaimedImport(
            internal_id=claim.internal_id,
            public_id=claim.public_id,
            fencing_token=claim.fencing_token + 1,
            source_sha256=claim.source_sha256,
            study_language=claim.study_language,
        )
        assert await wrong_owner._renew_lease(claim) is False
        assert await owner._renew_lease(wrong_fence) is False

        await _expire_claim(sessions, claim)
        assert await owner._renew_lease(claim) is False
        async with sessions() as session:
            record = (await session.execute(select(DocumentImportRecord))).scalar_one()
            assert record.lease_until is not None
            assert record.lease_until < datetime.now(UTC)
    finally:
        await engine.dispose()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["prepare_attempt_dir", "write_model_atomic"])
async def test_coordinator_enospc_maps_to_temp_storage_exhausted_without_partial_document(
    clean_postgres_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    settings = _settings(clean_postgres_url, tmp_path)
    import_id = await _admit_pdf(settings, idempotency_key=f"enospc-{failure_point}-001")
    engine, sessions = create_database(settings.require_database_url())
    spool = PdfSpool(settings.pdf_spool_root)
    coordinator = _coordinator(settings, sessions, worker_id="enospc-owner", spool=spool)

    def raise_enospc(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "simulated full filesystem")

    monkeypatch.setattr(spool, failure_point, raise_enospc)
    try:
        assert await coordinator.run_once() is True
        async with sessions() as session:
            assert await session.scalar(select(func.count(DocumentRecord.id))) == 0
            assert await session.scalar(select(func.count(PageRecord.id))) == 0
            assert await session.scalar(select(func.count(JobRecord.id))) == 0
            record = (await session.execute(select(DocumentImportRecord))).scalar_one()
            assert record.public_id == import_id
            assert record.status == "failed"
            assert record.error_code == "pdf_temp_storage_exhausted"
            assert record.error_code != "pdf_invalid"
            assert record.finished_at is not None
            assert record.source_cleaned_at is not None
        assert not spool.import_dir(import_id).exists()
        assert not spool.output_import_dir(import_id).exists()
    finally:
        await engine.dispose()
