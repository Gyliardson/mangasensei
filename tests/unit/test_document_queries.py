from __future__ import annotations

from mangasensei.application.document_queries import _progress


def test_document_progress_shape_uses_mutually_exclusive_base_counters() -> None:
    progress = _progress(total=4, completed=2, processing=1, failed=1)

    assert progress == {
        "totalPages": 4,
        "completedPages": 2,
        "processingPages": 1,
        "failedPages": 1,
    }
    assert (
        progress["completedPages"] + progress["processingPages"] + progress["failedPages"]
        == progress["totalPages"]
    )
