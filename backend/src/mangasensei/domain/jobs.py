"""Job state machine independent from persistence."""

from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    PROCESSING_OCR = "processing_ocr"
    PROCESSING_LINGUISTICS = "processing_linguistics"
    PROCESSING_GEMINI = "processing_gemini"
    COMPLETED = "completed"
    RETRYABLE_FAILURE = "retryable_failure"
    FAILED = "failed"
    EXPIRED = "expired"


class InvalidJobTransition(ValueError):
    """Raised when a worker attempts an invalid state change."""


_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.CLAIMED, JobStatus.EXPIRED}),
    JobStatus.CLAIMED: frozenset(
        {
            JobStatus.PROCESSING_OCR,
            JobStatus.PROCESSING_GEMINI,
            JobStatus.COMPLETED,
            JobStatus.RETRYABLE_FAILURE,
            JobStatus.FAILED,
            JobStatus.EXPIRED,
        }
    ),
    JobStatus.PROCESSING_OCR: frozenset(
        {
            JobStatus.PROCESSING_LINGUISTICS,
            JobStatus.RETRYABLE_FAILURE,
            JobStatus.FAILED,
            JobStatus.EXPIRED,
        }
    ),
    JobStatus.PROCESSING_LINGUISTICS: frozenset(
        {
            JobStatus.PROCESSING_GEMINI,
            JobStatus.COMPLETED,
            JobStatus.RETRYABLE_FAILURE,
            JobStatus.FAILED,
            JobStatus.EXPIRED,
        }
    ),
    JobStatus.PROCESSING_GEMINI: frozenset(
        {JobStatus.COMPLETED, JobStatus.RETRYABLE_FAILURE, JobStatus.FAILED, JobStatus.EXPIRED}
    ),
    JobStatus.RETRYABLE_FAILURE: frozenset(
        {JobStatus.CLAIMED, JobStatus.FAILED, JobStatus.EXPIRED}
    ),
    JobStatus.COMPLETED: frozenset({JobStatus.EXPIRED}),
    JobStatus.FAILED: frozenset({JobStatus.EXPIRED}),
    JobStatus.EXPIRED: frozenset(),
}


def transition_job(current: JobStatus, target: JobStatus) -> JobStatus:
    if target not in _TRANSITIONS[current]:
        raise InvalidJobTransition(f"invalid job transition: {current.value} -> {target.value}")
    return target
