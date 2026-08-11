from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from scripts.research_lab.contracts import RESULT_SENTINEL, STATUS_SENTINEL

_API_ROOT = "https://api.github.com"
_MAX_COMMENT_PAGES = 10


class GitHubApiError(RuntimeError):
    """Raised when the bounded Research Lab GitHub API call fails."""


def _request_json(
    *, token: str, method: str, url: str, payload: dict[str, Any] | None = None
) -> Any:
    if not url.startswith(f"{_API_ROOT}/repos/"):
        raise GitHubApiError("refusing non-GitHub API URL")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "mangasensei-research-lab-v1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
    ) as exc:
        raise GitHubApiError("bounded GitHub API request failed") from exc


def list_issue_comments(*, token: str, repository: str, issue_number: int) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    for page in range(1, _MAX_COMMENT_PAGES + 1):
        url = (
            f"{_API_ROOT}/repos/{repository}/issues/{issue_number}/comments"
            f"?per_page=100&page={page}"
        )
        payload = _request_json(token=token, method="GET", url=url)
        if not isinstance(payload, list):
            raise GitHubApiError("issue comments response was not a list")
        page_comments = [item for item in payload if isinstance(item, dict)]
        comments.extend(page_comments)
        if len(payload) < 100:
            return comments
    raise GitHubApiError("issue comment history exceeded bounded pagination limit")


def post_issue_comment(
    *, token: str, repository: str, issue_number: int, body: str
) -> dict[str, Any]:
    url = f"{_API_ROOT}/repos/{repository}/issues/{issue_number}/comments"
    payload = _request_json(token=token, method="POST", url=url, payload={"body": body})
    if not isinstance(payload, dict):
        raise GitHubApiError("create-comment response was not an object")
    return payload


def find_command_markers(comments: list[dict[str, Any]], command_id: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for comment in comments:
        body = comment.get("body")
        if not isinstance(body, str):
            continue
        lines = body.splitlines()
        if len(lines) < 2 or lines[0] not in {STATUS_SENTINEL, RESULT_SENTINEL}:
            continue
        try:
            payload = json.loads("\n".join(lines[1:]))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("command_id") == command_id:
            matches.append(
                {
                    "comment_id": comment.get("id"),
                    "sentinel": lines[0],
                    "state": payload.get("state"),
                    "run_id": payload.get("run_id"),
                }
            )
    return matches
