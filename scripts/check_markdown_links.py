"""Validate repository-local Markdown links without making network requests."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
IGNORED_PREFIXES = ("http://", "https://", "mailto:", "#")


def tracked_markdown_files() -> tuple[Path, ...]:
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable not found")
    completed = subprocess.run(
        [git, "ls-files", "*.md"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return tuple(ROOT / line for line in completed.stdout.splitlines() if line)


def target_path(document: Path, raw_target: str) -> Path | None:
    target = raw_target.strip()
    if not target or target.startswith(IGNORED_PREFIXES):
        return None
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    target = target.split("#", 1)[0]
    if not target:
        return None
    decoded = unquote(target)
    return (document.parent / decoded).resolve()


def main() -> int:
    failures: list[str] = []
    for document in tracked_markdown_files():
        text = document.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            raw_target = match.group(1)
            target = target_path(document, raw_target)
            if target is None:
                continue
            try:
                target.relative_to(ROOT)
            except ValueError:
                failures.append(
                    f"{document.relative_to(ROOT)}: link escapes repository: {raw_target}"
                )
                continue
            if not target.exists():
                failures.append(
                    f"{document.relative_to(ROOT)}: missing local target: {raw_target}"
                )

    if failures:
        print("Broken repository-local Markdown links:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print("Repository-local Markdown links are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
