from mangasensei.gemini.errors import (
    GeminiProviderError,
    GeminiProviderFailureKind,
    GeminiResponseError,
)
from mangasensei.workers.runner import (
    GeminiDailyBudgetExceededError,
    GeminiPageCallLimitExceededError,
    _is_retryable_pipeline_failure,
)


def test_worker_failure_policy_keeps_only_recoverable_gemini_failures_retryable() -> None:
    permanent_request = GeminiProviderError(
        kind=GeminiProviderFailureKind.REQUEST,
        retryable=False,
        status_code=400,
    )
    transient_provider = GeminiProviderError(
        kind=GeminiProviderFailureKind.SERVER,
        retryable=True,
        status_code=503,
    )

    assert _is_retryable_pipeline_failure(permanent_request) is False
    assert _is_retryable_pipeline_failure(transient_provider) is True
    assert _is_retryable_pipeline_failure(GeminiResponseError("synthetic malformed output")) is True
    assert _is_retryable_pipeline_failure(GeminiDailyBudgetExceededError()) is False
    assert _is_retryable_pipeline_failure(GeminiPageCallLimitExceededError()) is False
