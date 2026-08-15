from __future__ import annotations

from fractions import Fraction

from scripts.reading_order_v2.canonical import canonical_json_bytes, decimal_text, sha256_bytes


def test_canonical_json_ignores_dict_insertion_order() -> None:
    left = {"b": 2, "a": {"y": 2, "x": 1}}
    right = {"a": {"x": 1, "y": 2}, "b": 2}
    assert canonical_json_bytes(left) == canonical_json_bytes(right)
    assert sha256_bytes(canonical_json_bytes(left)) == sha256_bytes(canonical_json_bytes(right))
    assert canonical_json_bytes(left).endswith(b"\n")


def test_fraction_decimal_is_presentation_only_and_stable() -> None:
    assert decimal_text(Fraction(98, 103)) == "0.951456310680"
