from __future__ import annotations

from mangasensei.workers.runner import _safe_exception_context


def test_safe_exception_context_keeps_types_and_locations_without_messages() -> None:
    sensitive_markers = (
        "CAPABILITY_TOKEN_SHOULD_NOT_APPEAR",
        "API_KEY_SHOULD_NOT_APPEAR",
        "postgresql://user:password@database/mangasensei",
        "IMAGE_BYTES_SHOULD_NOT_APPEAR",
        "秘密のOCR本文",
    )

    try:
        try:
            raise ValueError(" | ".join(sensitive_markers))
        except ValueError as cause:
            raise RuntimeError("outer diagnostic must also stay private") from cause
    except RuntimeError as exc:
        context = _safe_exception_context(exc)

    assert "RuntimeError@" in context
    assert "ValueError@" in context
    assert "test_worker_observability.py:" in context
    assert "test_safe_exception_context_keeps_types_and_locations_without_messages" in context
    assert "outer diagnostic must also stay private" not in context
    for marker in sensitive_markers:
        assert marker not in context
