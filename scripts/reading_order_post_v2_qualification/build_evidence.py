from __future__ import annotations

import argparse
from pathlib import Path

from .evidence import build_evidence


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic post-v2 evidence bundle")
    parser.add_argument("--experiment-spec", type=Path, required=True)
    parser.add_argument("--expected-spec-sha256", required=True)
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--environment-json", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--zip-path", type=Path, required=True)
    args = parser.parse_args()
    build_evidence(
        spec_path=args.experiment_spec,
        expected_spec_sha256=args.expected_spec_sha256,
        corpus_root=args.corpus_root,
        environment_json=args.environment_json,
        output_root=args.output_root,
        staging=args.staging_dir,
        destination_zip=args.zip_path,
    )


if __name__ == "__main__":
    main()
