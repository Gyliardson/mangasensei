from __future__ import annotations

from fractions import Fraction

import pytest
from scripts.reading_order_v2.comparison import (
    REPEAT_HASH_FIELDS,
    build_repeat_hash_record,
    require_repeat_determinism,
)
from scripts.reading_order_v2.scoring import CorpusScore, PairMetrics


def _metric() -> PairMetrics:
    return PairMetrics(1, 0, 1, Fraction(1, 1), Fraction(0, 1), ())


def _score() -> CorpusScore:
    return CorpusScore(16, 16, _metric(), {"A": _metric()}, ())


def test_repeat_hash_record_contains_all_frozen_hash_classes() -> None:
    record = build_repeat_hash_record([{"pageId": "H01"}], [{"pageId": "H01"}], _score())
    assert tuple(record) == REPEAT_HASH_FIELDS
    assert all(len(record[field]) == 64 for field in REPEAT_HASH_FIELDS)


def test_deterministic_repeat_hashes_pass() -> None:
    record = build_repeat_hash_record([{"pageId": "H01"}], [{"pageId": "H01"}], _score())
    require_repeat_determinism("A0_B0_CONTROL", [record, dict(record), dict(record)])


@pytest.mark.parametrize(
    "field",
    ["diagnosticsSha256", "orderingSha256", "scoresSha256"],
)
def test_each_repeat_hash_class_mismatch_fails_closed(field: str) -> None:
    record = build_repeat_hash_record([{"pageId": "H01"}], [{"pageId": "H01"}], _score())
    changed = dict(record)
    changed[field] = "f" * 64 if record[field] != "f" * 64 else "e" * 64
    with pytest.raises(RuntimeError, match="nondeterministic evidence"):
        require_repeat_determinism("A0_B0_CONTROL", [record, dict(record), changed])
