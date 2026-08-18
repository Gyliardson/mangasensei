from __future__ import annotations

import argparse
from pathlib import Path

from .contracts import ContractError
from .validate import validate_corpus, write_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Reading Order v3 clean-room authoring tooling")
    parser.add_argument("command", choices=("build-manifest", "validate"))
    parser.add_argument("corpus_root", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "build-manifest":
            path = write_manifest(args.corpus_root)
            print(path)
        else:
            coverage = validate_corpus(args.corpus_root)
            print(
                "valid: "
                f"dedicated-positive-families={len(coverage.dedicated_positive_pages)} "
                f"c3-rejection-pages={len(coverage.c3_rejection_pages)}"
            )
    except ContractError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
