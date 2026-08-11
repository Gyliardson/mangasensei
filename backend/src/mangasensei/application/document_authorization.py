"""Document-scoped capability authorization for aggregates and child resources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.application.authorization import (
    AuthorizedImage,
    AuthorizedPage,
    ResourceNotFoundError,
)
from mangasensei.domain.capabilities import DocumentCapabilityScope
from mangasensei.infrastructure.capabilities import CapabilityService
from mangasensei.infrastructure.database.document_models import (
    DocumentCapabilityRecord,
    DocumentRecord,
)
from mangasensei.infrastructure.database.storage_models import ImageBlobRecord, PageRecord


@dataclass(frozen=True, slots=True)
class AuthorizedDocument:
    internal_id: int
    public_id: UUID
    source_kind: str
    order_revision: int
    expires_at: datetime


class DocumentAuthorizer:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        capabilities: CapabilityService,
    ) -> None:
        self._sessions = sessions
        self._capabilities = capabilities

    async def authorize_document(self, *, document_id: UUID, token: str) -> AuthorizedDocument:
        async with self._sessions() as session:
            return await self._authorize_document_scope(
                session,
                document_id=document_id,
                token=token,
                scope=DocumentCapabilityScope.READ_DOCUMENT,
            )

    async def authorize_manage(self, *, document_id: UUID, token: str) -> AuthorizedDocument:
        async with self._sessions() as session:
            return await self._authorize_document_scope(
                session,
                document_id=document_id,
                token=token,
                scope=DocumentCapabilityScope.MANAGE_DOCUMENT,
            )

    async def authorize_page(
        self,
        *,
        document_id: UUID,
        page_id: UUID,
        token: str,
    ) -> AuthorizedPage:
        async with self._sessions() as session:
            document = await self._authorize_document_scope(
                session,
                document_id=document_id,
                token=token,
                scope=DocumentCapabilityScope.READ_DOCUMENT,
            )
            return await self._authorize_member_page(session, document.internal_id, page_id)

    async def authorize_reprocess(
        self,
        *,
        document_id: UUID,
        page_id: UUID,
        token: str,
    ) -> AuthorizedPage:
        async with self._sessions() as session:
            document = await self._authorize_document_scope(
                session,
                document_id=document_id,
                token=token,
                scope=DocumentCapabilityScope.REPROCESS_DOCUMENT,
            )
            return await self._authorize_member_page(session, document.internal_id, page_id)

    async def authorize_image(
        self,
        *,
        document_id: UUID,
        page_id: UUID,
        token: str,
    ) -> AuthorizedImage:
        async with self._sessions() as session:
            document = await self._authorize_document_scope(
                session,
                document_id=document_id,
                token=token,
                scope=DocumentCapabilityScope.READ_DOCUMENT_IMAGE,
            )
            row = (
                await session.execute(
                    select(PageRecord, ImageBlobRecord)
                    .join(ImageBlobRecord, ImageBlobRecord.id == PageRecord.image_blob_id)
                    .where(
                        PageRecord.public_id == page_id,
                        PageRecord.document_id == document.internal_id,
                        PageRecord.expires_at > datetime.now(UTC),
                        ImageBlobRecord.state == "ready",
                    )
                )
            ).one_or_none()
            if row is None:
                raise ResourceNotFoundError
            page, blob = row
            return AuthorizedImage(
                storage_key=blob.storage_key,
                media_type=blob.media_type,
                filename=page.original_filename,
            )

    async def _authorize_member_page(
        self,
        session: AsyncSession,
        document_id: int,
        page_id: UUID,
    ) -> AuthorizedPage:
        page = (
            await session.execute(
                select(PageRecord).where(
                    PageRecord.public_id == page_id,
                    PageRecord.document_id == document_id,
                    PageRecord.expires_at > datetime.now(UTC),
                )
            )
        ).scalar_one_or_none()
        if page is None:
            raise ResourceNotFoundError
        return AuthorizedPage(
            internal_id=page.id,
            public_id=page.public_id,
            expires_at=page.expires_at,
        )

    async def _authorize_document_scope(
        self,
        session: AsyncSession,
        *,
        document_id: UUID,
        token: str,
        scope: DocumentCapabilityScope,
    ) -> AuthorizedDocument:
        document = (
            await session.execute(
                select(DocumentRecord).where(
                    DocumentRecord.public_id == document_id,
                    DocumentRecord.expires_at > datetime.now(UTC),
                )
            )
        ).scalar_one_or_none()
        if document is None:
            raise ResourceNotFoundError
        capabilities = (
            await session.execute(
                select(DocumentCapabilityRecord).where(
                    DocumentCapabilityRecord.document_id == document.id,
                    DocumentCapabilityRecord.scope == scope.value,
                    DocumentCapabilityRecord.revoked_at.is_(None),
                    DocumentCapabilityRecord.expires_at > datetime.now(UTC),
                )
            )
        ).scalars()
        if not any(
            self._capabilities.verify(
                token=token,
                persisted_digest=capability.digest.hex(),
                resource_id=str(document.public_id),
                scope=scope,
                expires_at=capability.expires_at,
            )
            for capability in capabilities
        ):
            raise ResourceNotFoundError
        return AuthorizedDocument(
            internal_id=document.id,
            public_id=document.public_id,
            source_kind=document.source_kind,
            order_revision=document.order_revision,
            expires_at=document.expires_at,
        )
