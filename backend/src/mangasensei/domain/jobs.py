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
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class InvalidJobTransition(ValueError):
    """Raised when a worker attempts an invalid state change."""


_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.CLAIMED, JobStatus.CANCELLED, JobStatus.EXPIRED}),
    JobStatus.CLAIMED: frozenset(
        {
            JobStatus.PROCESSING_OCR,
            JobStatus.RETRYABLE_FAILURE,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.EXPIRED,
        }
    ),
    JobStatus.PROCESSING_OCR: frozenset(
        {
            JobStatus.PROCESSING_LINGUISTICS,
            JobStatus.RETRYABLE_FAILURE,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.EXPIRED,
        }
    ),
    JobStatus.PROCESSING_LINGUISTICS: frozenset(
        {
            JobStatus.PROCESSING_GEMINI,
            JobStatus.COMPLETED,
            JobStatus.RETRYABLE_FAILURE,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.EXPIRED,
        }
    ),
    JobStatus.PROCESSING_GEMINI: frozenset(
        {
            JobStatus.COMPLETED,
            JobStatus.RETRYABLE_FAILURE,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.EXPIRED,
        }
    ),
    JobStatus.RETRYABLE_FAILURE: frozenset(
        {JobStatus.CLAIMED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.EXPIRED}
    ),
    JobStatus.COMPLETED: frozenset({JobStatus.EXPIRED}),
    JobStatus.FAILED: frozenset({JobStatus.EXPIRED}),
    JobStatus.CANCELLED: frozenset({JobStatus.EXPIRED}),
    JobStatus.EXPIRED: frozenset(),
}

_STUDY_LANGUAGE_REUSE_DIRECT_TRANSITIONS = frozenset(
    {JobStatus.PROCESSING_GEMINI, JobStatus.COMPLETED}
)
_DICTIONARY_PROJECTION_DIRECT_TRANSITIONS = frozenset({JobStatus.COMPLETED})


def transition_job(current: JobStatus, target: JobStatus) -> JobStatus:
    if target not in _TRANSITIONS[current]:
        raise InvalidJobTransition(f"invalid job transition: {current.value} -> {target.value}")
    return target


def transition_study_language_reuse_job(current: JobStatus, target: JobStatus) -> JobStatus:
    """Allow a reuse job to skip OCR/linguistics without weakening normal jobs."""

    if current is JobStatus.CLAIMED and target in _STUDY_LANGUAGE_REUSE_DIRECT_TRANSITIONS:
        return target
    return transition_job(current, target)


def transition_dictionary_projection_job(current: JobStatus, target: JobStatus) -> JobStatus:
    """Allow dictionary-only projection to complete directly from a fenced claim."""

    if current is JobStatus.CLAIMED and target in _DICTIONARY_PROJECTION_DIRECT_TRANSITIONS:
        return target
    return transition_job(current, target)