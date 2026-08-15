from __future__ import annotations

import json
import os
import subprocess
import sys
from fractions import Fraction

from scripts.reading_order_v2.canonical import (
    canonical_json_bytes,
    fraction_record,
    sha256_bytes,
)


def test_canonical_json_ignores_dict_insertion_order_and_recreated_objects() -> None:
    first = {"z": [3, 2, 1], "a": {"y": 2, "x": 1}}
    second = {"a": {"x": 1, "y": 2}, "z": [3, 2, 1]}
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert canonical_json_bytes(first).endswith(b"\n")
    assert sha256_bytes(canonical_json_bytes(first)) == sha256_bytes(
        canonical_json_bytes(second)
    )


def test_fraction_serialization_uses_exact_ratio_and_fixed_presentation_decimal() -> None:
    assert fraction_record(Fraction(1, 3)) == {
        "decimal": "0.333333333333",
        "denominator": 3,
        "numerator": 1,
    }
    assert fraction_record(Fraction(1, 8))["decimal"] == "0.125000000000"


def test_canonical_json_is_hash_seed_independent() -> None:
    code = (
        "from scripts.reading_order_v2.canonical import canonical_json_bytes; "
        "import sys; sys.stdout.buffer.write(canonical_json_bytes({'b':2,'a':1}))"
    )
    outputs = []
    for seed in ("1", "41", "909"):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        outputs.append(subprocess.check_output([sys.executable, "-c", code], env=env))
    assert len(set(outputs)) == 1
    assert json.loads(outputs[0]) == {"a": 1, "b": 2}
