"""Measure one-process JMdict runtime load cost without enforcing product limits."""

from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

from mangasensei.linguistics.jmdict import JsonJmdictDictionary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dictionary", type=Path)
    args = parser.parse_args()

    started = time.perf_counter()
    dictionary = JsonJmdictDictionary(args.dictionary)
    elapsed = time.perf_counter() - started
    max_rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    print(
        json.dumps(
            {
                "entry_count": dictionary.entry_count,
                "file_size_bytes": args.dictionary.stat().st_size,
                "load_seconds": round(elapsed, 6),
                "max_rss_kib": max_rss_kib,
                "method": (
                    "Python resource.getrusage(RUSAGE_SELF).ru_maxrss on Linux; "
                    "one fresh process per pack"
                ),
                "path": str(args.dictionary),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
