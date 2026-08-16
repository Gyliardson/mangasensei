from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(".github/workflows/reading-order-v2-qualification.yml")
EXECUTED_STAGE1_SHA = "78838d21e9657c7b854178b1d2d7c73d56bcbc57"


def test_qualification_workflow_is_manual_read_only_and_fail_closed() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "permissions:\n  contents: read" in text
    assert "persist-credentials: false" in text
    assert "authorize_new_qualification" in text
    assert EXECUTED_STAGE1_SHA in text
    assert "replay is forbidden" in text
    assert "uv sync --frozen --extra ocr" in text
    assert "uv run python -m scripts.reading_order_v2.run_heldout" in text
    assert "scripts.reading_order_v2.build_evidence" in text


def test_qualification_workflow_uploads_execution_and_evidence_artifacts() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    upload_pin = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
    assert text.count(upload_pin) == 2
    assert "reading-order-v2-${{ inputs.qualification_id }}" in text
    assert "retention-days: 90" in text
    assert "if: always()" in text
    assert "if: success()" in text
