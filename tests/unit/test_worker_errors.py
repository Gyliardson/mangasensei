from __future__ import annotations

from mangasensei.workers.runner import (
    GeminiBudgetExceededError,
    StaleLeaseError,
    _public_error_code,
)


def test_budget_exceeded_maps_to_stable_public_code() -> None:
    assert _public_error_code(GeminiBudgetExceededError()) == "gemini_budget_exceeded"


def test_timeout_maps_to_stable_public_code() -> None:
    assert _public_error_code(TimeoutError()) == "provider_timeout"


def test_unknown_internal_error_maps_to_generic_public_code() -> None:
    class _InternalLeak(Exception): ...

    assert _public_error_code(_InternalLeak()) == "processing_failed"


def test_stale_lease_is_not_leaked_through_public_code() -> None:
    assert _public_error_code(StaleLeaseError()) == "processing_failed"
