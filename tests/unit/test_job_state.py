import pytest

from mangasensei.domain.jobs import (
    InvalidJobTransition,
    JobStatus,
    transition_dictionary_projection_job,
    transition_job,
)


def test_job_happy_path_is_explicit() -> None:
    state = JobStatus.PENDING
    for expected in (
        JobStatus.CLAIMED,
        JobStatus.PROCESSING_OCR,
        JobStatus.PROCESSING_LINGUISTICS,
        JobStatus.PROCESSING_GEMINI,
        JobStatus.COMPLETED,
    ):
        state = transition_job(state, expected)
    assert state is JobStatus.COMPLETED


def test_completed_job_cannot_return_to_processing() -> None:
    with pytest.raises(InvalidJobTransition):
        transition_job(JobStatus.COMPLETED, JobStatus.PROCESSING_OCR)


def test_generic_claimed_job_cannot_complete_directly() -> None:
    with pytest.raises(InvalidJobTransition):
        transition_job(JobStatus.CLAIMED, JobStatus.COMPLETED)


def test_dictionary_projection_can_complete_directly_from_claimed() -> None:
    assert (
        transition_dictionary_projection_job(JobStatus.CLAIMED, JobStatus.COMPLETED)
        is JobStatus.COMPLETED
    )


def test_retryable_job_can_be_claimed_without_promotion_race() -> None:
    assert transition_job(JobStatus.RETRYABLE_FAILURE, JobStatus.CLAIMED) is JobStatus.CLAIMED


@pytest.mark.parametrize(
    "status",
    [
        JobStatus.PENDING,
        JobStatus.CLAIMED,
        JobStatus.PROCESSING_OCR,
        JobStatus.PROCESSING_LINGUISTICS,
        JobStatus.PROCESSING_GEMINI,
        JobStatus.RETRYABLE_FAILURE,
    ],
)
def test_unfinished_job_can_be_cancelled(status: JobStatus) -> None:
    assert transition_job(status, JobStatus.CANCELLED) is JobStatus.CANCELLED


def test_terminal_jobs_cannot_be_rewritten_as_cancelled() -> None:
    for status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.EXPIRED):
        with pytest.raises(InvalidJobTransition):
            transition_job(status, JobStatus.CANCELLED)


@pytest.mark.parametrize(
    "status",
    [
        JobStatus.CLAIMED,
        JobStatus.PROCESSING_OCR,
        JobStatus.PROCESSING_LINGUISTICS,
        JobStatus.PROCESSING_GEMINI,
        JobStatus.RETRYABLE_FAILURE,
        JobStatus.CANCELLED,
    ],
)
def test_janitor_can_expire_every_nonterminal_or_cancelled_job(status: JobStatus) -> None:
    assert transition_job(status, JobStatus.EXPIRED) is JobStatus.EXPIRED