from datetime import UTC, datetime, timedelta

from mangasensei.domain.capabilities import DocumentCapabilityScope
from mangasensei.infrastructure.capabilities import CapabilityService


def test_document_capability_is_resource_and_scope_bound() -> None:
    service = CapabilityService(("d" * 32,))
    issued = service.issue(
        resource_id="document-123",
        scope=DocumentCapabilityScope.READ_DOCUMENT,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )

    assert service.verify(
        token=issued.token,
        persisted_digest=issued.persisted_digest,
        resource_id="document-123",
        scope=DocumentCapabilityScope.READ_DOCUMENT,
        expires_at=issued.expires_at,
    )
    assert not service.verify(
        token=issued.token,
        persisted_digest=issued.persisted_digest,
        resource_id="document-123",
        scope=DocumentCapabilityScope.READ_DOCUMENT_IMAGE,
        expires_at=issued.expires_at,
    )
    assert not service.verify(
        token=issued.token,
        persisted_digest=issued.persisted_digest,
        resource_id="document-456",
        scope=DocumentCapabilityScope.READ_DOCUMENT,
        expires_at=issued.expires_at,
    )
