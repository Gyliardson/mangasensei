from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

EXECUTION_STEP_NAME = "Execute frozen qualification exactly once"
RUN_TITLE_PREFIX = "Reading Order Post-v2 Qualification · "


def _request_json(url: str, *, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mangasensei-post-v2-replay-guard",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub API returned a non-object payload")
    return payload


def execution_step_observed(job_payload: dict[str, Any]) -> bool:
    jobs = job_payload.get("jobs")
    if not isinstance(jobs, list):
        raise RuntimeError("GitHub jobs payload is malformed")
    for job in jobs:
        if not isinstance(job, dict):
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict) or step.get("name") != EXECUTION_STEP_NAME:
                continue
            status = step.get("status")
            conclusion = step.get("conclusion")
            if status == "in_progress":
                return True
            if status == "completed" and conclusion not in {None, "skipped"}:
                return True
    return False


def detect_duplicate(
    *,
    runs: list[dict[str, Any]],
    qualification_identity: str,
    current_run_id: int,
    jobs_loader: Callable[[int], dict[str, Any]],
) -> int | None:
    expected_title = f"{RUN_TITLE_PREFIX}{qualification_identity}"
    for run in runs:
        if not isinstance(run, dict):
            continue
        run_id = run.get("id")
        if not isinstance(run_id, int) or run_id == current_run_id:
            continue
        if run.get("display_title") != expected_title:
            continue
        jobs = jobs_loader(run_id)
        if execution_step_observed(jobs):
            return run_id
    return None


def assert_not_replayed(
    *,
    repository: str,
    workflow_file: str,
    qualification_identity: str,
    current_run_id: int,
    token: str,
    api_url: str,
) -> None:
    owner_repo = urllib.parse.quote(repository, safe="/")
    workflow = urllib.parse.quote(workflow_file, safe="")
    runs: list[dict[str, Any]] = []
    page = 1
    while True:
        url = (
            f"{api_url}/repos/{owner_repo}/actions/workflows/{workflow}/runs"
            f"?event=workflow_dispatch&per_page=100&page={page}"
        )
        payload = _request_json(url, token=token)
        batch = payload.get("workflow_runs")
        if not isinstance(batch, list):
            raise RuntimeError("GitHub workflow runs payload is malformed")
        runs.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < 100:
            break
        page += 1
        if page > 100:
            raise RuntimeError("replay guard pagination safety limit exceeded")

    def load_jobs(run_id: int) -> dict[str, Any]:
        return _request_json(
            f"{api_url}/repos/{owner_repo}/actions/runs/{run_id}/jobs?per_page=100",
            token=token,
        )

    duplicate = detect_duplicate(
        runs=runs,
        qualification_identity=qualification_identity,
        current_run_id=current_run_id,
        jobs_loader=load_jobs,
    )
    if duplicate is not None:
        raise RuntimeError(
            "duplicate/replay rejected: qualification identity was already executed "
            f"in run {duplicate}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fail closed on an observed qualification identity"
    )
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-file", required=True)
    parser.add_argument("--qualification-identity", required=True)
    parser.add_argument("--current-run-id", required=True, type=int)
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required for replay guard")
    assert_not_replayed(
        repository=args.repository,
        workflow_file=args.workflow_file,
        qualification_identity=args.qualification_identity,
        current_run_id=args.current_run_id,
        token=token,
        api_url=api_url.rstrip("/"),
    )


if __name__ == "__main__":
    main()
