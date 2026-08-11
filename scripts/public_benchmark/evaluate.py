from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

_HEX40 = re.compile(r"^[0-9a-f]{40}$")


def _git_dir(repo_root: Path) -> Path:
    git_entry = repo_root / ".git"
    if git_entry.is_dir():
        return git_entry
    if git_entry.is_file():
        content = git_entry.read_text(encoding="utf-8").strip()
        prefix = "gitdir: "
        if not content.startswith(prefix):
            raise RuntimeError("unsupported .git file format")
        target = Path(content[len(prefix) :])
        return target if target.is_absolute() else (repo_root / target).resolve()
    raise RuntimeError("repository .git metadata not found")


def _read_ref(git_dir: Path, reference: str) -> str:
    loose_ref = git_dir / reference
    if loose_ref.is_file():
        return loose_ref.read_text(encoding="ascii").strip()
    packed_refs = git_dir / "packed-refs"
    if packed_refs.is_file():
        for line in packed_refs.read_text(encoding="ascii").splitlines():
            if not line or line.startswith(("#", "^")):
                continue
            sha, name = line.split(" ", 1)
            if name == reference:
                return sha
    raise RuntimeError(f"Git reference not found: {reference}")


def resolve_repository_sha(repo_root: Path, explicit: str | None) -> str:
    candidates = [
        explicit,
        os.environ.get("MANGASENSEI_EVALUATOR_REPOSITORY_SHA"),
        os.environ.get("GITHUB_SHA"),
    ]
    for candidate in candidates:
        if candidate:
            if _HEX40.fullmatch(candidate) is None:
                raise RuntimeError(
                    "evaluator repository SHA must be 40 lowercase hexadecimal characters"
                )
            return candidate

    git_dir = _git_dir(repo_root)
    head = (git_dir / "HEAD").read_text(encoding="ascii").strip()
    sha = _read_ref(git_dir, head[5:]) if head.startswith("ref: ") else head
    if _HEX40.fullmatch(sha) is None:
        raise RuntimeError("could not resolve a valid evaluator repository SHA")
    return sha


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Deterministically score a frozen MangaSensei public OCR observation."
    )
    parser.add_argument("--corpus", type=Path, required=True, help="Public demo corpus directory")
    parser.add_argument("--observations", type=Path, required=True, help="Frozen observation JSON")
    parser.add_argument("--output", type=Path, required=True, help="Destination report JSON")
    parser.add_argument(
        "--evaluator-repository-sha",
        help="Override evaluator Git SHA; otherwise resolve from environment or checkout metadata",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from scripts.public_benchmark.contracts import bind_observation
    from scripts.public_benchmark.corpus import load_corpus
    from scripts.public_benchmark.observation import load_observation
    from scripts.public_benchmark.report import build_report, serialize_report

    evaluator_sha = resolve_repository_sha(repo_root, args.evaluator_repository_sha)
    corpus = load_corpus(args.corpus)
    observation = load_observation(args.observations)
    bind_observation(corpus, observation)
    report_bytes = serialize_report(
        build_report(corpus, observation, evaluator_repository_sha=evaluator_sha)
    )

    output: Path = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{output.name}.",
            suffix=".tmp",
            dir=output.parent,
            delete=False,
        ) as handle:
            handle.write(report_bytes)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, output)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
