from __future__ import annotations

from pathlib import Path


def test_research_workflow_is_fail_closed_and_least_privilege() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/research-lab.yml").read_text(encoding="utf-8")
    assert "issue_comment:" in workflow
    assert "types: [created]" in workflow
    assert "pull_request_target" not in workflow
    assert "contents: read" in workflow
    assert "issues: write" in workflow
    assert "persist-credentials: false" in workflow
    assert "timeout-minutes: 5" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "github.event.issue.number == 132" in workflow
    assert "github.event.comment.user.login == 'Gyliardson'" in workflow
    assert "-m scripts.research_lab.validate_event" in workflow
    assert "-m scripts.research_lab.run_experiment" in workflow
    assert "-m scripts.research_lab.post_comment" in workflow
    assert "--event \"$GITHUB_EVENT_PATH\"" in workflow
    assert "${{ github.event.comment.body }}" not in workflow
    assert "retention-days: 30" in workflow
    assert "always()" in workflow
    assert "--kind failure" in workflow
    assert "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1" in workflow
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in workflow
