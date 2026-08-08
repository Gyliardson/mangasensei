"""Page-scoped capability authorization."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from mangasensei.domain.capabilities import CapabilityScope
from mangasensei.infrastructure.capabilities import CapabilityService
from mangasensei.infrastructure.database.storage_models import (
    ImageBlobRecord,
    PageCapabilityRecord,
    PageRecord,
)


class ResourceNotFoundError(LookupError):
    """Uniform response for missing, expired or unauthorized resources."""


@dataclass(frozen=True, slots=True)
class AuthorizedImage:
    storage_key: str
    media_type: str
    filename: str


@dataclass(frozen=True, slots=True)
class AuthorizedPage:
    internal_id: int
    public_id: UUID
    expires_at: datetime


class PageAuthorizer:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        capabilities: CapabilityService,
    ) -> None:
        self._sessions = sessions
        self._capabilities = capabilities

    async def authorize_image(self, *, page_id: UUID, token: str) -> AuthorizedImage:
        async with self._sessions() as session:
            page = (
                await session.execute(
                    select(PageRecord, ImageBlobRecord)
                    .join(ImageBlobRecord, ImageBlobRecord.id == PageRecord.image_blob_id)
                    .where(
                        PageRecord.public_id == page_id,
                        PageRecord.expires_at > datetime.now(UTC),
                        ImageBlobRecord.state == "ready",
                    )
                )
            ).one_or_none()
            if page is None:
                raise ResourceNotFoundError
            page_record, blob = page
            capabilities = (
                await session.execute(
                    select(PageCapabilityRecord).where(
                        PageCapabilityRecord.page_id == page_record.id,
                        PageCapabilityRecord.scope == CapabilityScope.READ_IMAGE.value,
                        PageCapabilityRecord.revoked_at.is_(None),
                        PageCapabilityRecord.expires_at > datetime.now(UTC),
                    )
                )
            ).scalars()
            if not any(
                self._capabilities.verify(
                    token=token,
                    persisted_digest=capability.digest.hex(),
                    resource_id=str(page_record.public_id),
                    scope=CapabilityScope.READ_IMAGE,
                    expires_at=capability.expires_at,
                )
                for capability in capabilities
            ):
                raise ResourceNotFoundError
            return AuthorizedImage(
                storage_key=blob.storage_key,
                media_type=blob.media_type,
                filename=page_record.original_filename,
            )

    async def authorize_page(self, *, page_id: UUID, token: str) -> AuthorizedPage:
        return await self._authorize_page_scope(
            page_id=page_id,
            token=token,
            scope=CapabilityScope.READ_PAGE,
        )

    async def authorize_reprocess(self, *, page_id: UUID, token: str) -> AuthorizedPage:
        return await self._authorize_page_scope(
            page_id=page_id,
            token=token,
            scope=CapabilityScope.REPROCESS_PAGE,
        )

    async def _authorize_page_scope(
        self,
        *,
        page_id: UUID,
        token: str,
        scope: CapabilityScope,
    ) -> AuthorizedPage:
        async with self._sessions() as session:
            page_record = (
                await session.execute(
                    select(PageRecord).where(
                        PageRecord.public_id == page_id,
                        PageRecord.expires_at > datetime.now(UTC),
                    )
                )
            ).scalar_one_or_none()
            if page_record is None:
                raise ResourceNotFoundError
            capabilities = (
                await session.execute(
                    select(PageCapabilityRecord).where(
                        PageCapabilityRecord.page_id == page_record.id,
                        PageCapabilityRecord.scope == scope.value,
                        PageCapabilityRecord.revoked_at.is_(None),
                        PageCapabilityRecord.expires_at > datetime.now(UTC),
                    )
                )
            ).scalars()
            if not any(
                self._capabilities.verify(
                    token=token,
                    persisted_digest=capability.digest.hex(),
                    resource_id=str(page_record.public_id),
                    scope=scope,
                    expires_at=capability.expires_at,
                )
                for capability in capabilities
            ):
                raise ResourceNotFoundError
            return AuthorizedPage(
                internal_id=page_record.id,
                public_id=page_record.public_id,
                expires_at=page_record.expires_at,
            )
