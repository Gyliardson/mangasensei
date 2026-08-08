import pytest

from mangasensei.domain.jobs import InvalidJobTransition, JobStatus, transition_job


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


def test_retryable_job_can_be_claimed_without_promotion_race() -> None:
    assert transition_job(JobStatus.RETRYABLE_FAILURE, JobStatus.CLAIMED) is JobStatus.CLAIMED


@pytest.mark.parametrize(
    "status",
    [
        JobStatus.CLAIMED,
        JobStatus.PROCESSING_OCR,
        JobStatus.PROCESSING_LINGUISTICS,
        JobStatus.PROCESSING_GEMINI,
        JobStatus.RETRYABLE_FAILURE,
    ],
)
def test_janitor_can_expire_every_nonterminal_job(status: JobStatus) -> None:
    assert transition_job(status, JobStatus.EXPIRED) is JobStatus.EXPIRED
