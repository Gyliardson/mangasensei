from datetime import UTC, datetime, timedelta

from mangasensei.domain.capabilities import CapabilityScope
from mangasensei.infrastructure.capabilities import CapabilityService


def test_capability_is_resource_scoped_and_only_digest_is_persisted() -> None:
    service = CapabilityService(("a" * 32,))
    issued = service.issue(
        resource_id="page-123",
        scope=CapabilityScope.READ_PAGE,
        expires_at=datetime.now(UTC) + timedelta(hours=24),
    )

    assert issued.token not in issued.persisted_digest
    assert service.verify(
        token=issued.token,
        persisted_digest=issued.persisted_digest,
        resource_id="page-123",
        scope=CapabilityScope.READ_PAGE,
        expires_at=issued.expires_at,
    )
    assert not service.verify(
        token=issued.token,
        persisted_digest=issued.persisted_digest,
        resource_id="page-456",
        scope=CapabilityScope.READ_PAGE,
        expires_at=issued.expires_at,
    )


def test_capability_supports_pepper_rotation_and_rejects_expired_tokens() -> None:
    old_service = CapabilityService(("o" * 32,))
    issued = old_service.issue(
        resource_id="page-123",
        scope=CapabilityScope.READ_IMAGE,
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    rotated = CapabilityService(("n" * 32, "o" * 32))

    assert rotated.verify(
        token=issued.token,
        persisted_digest=issued.persisted_digest,
        resource_id="page-123",
        scope=CapabilityScope.READ_IMAGE,
        expires_at=issued.expires_at,
    )
    assert not rotated.verify(
        token=issued.token,
        persisted_digest=issued.persisted_digest,
        resource_id="page-123",
        scope=CapabilityScope.READ_IMAGE,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
