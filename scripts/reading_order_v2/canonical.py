from __future__ import annotations

import dataclasses
import hashlib
import json
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from enum import Enum
from fractions import Fraction
from pathlib import Path

_DECIMAL_PLACES = Decimal("0.000000000001")


def to_jsonable(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Fraction):
        return fraction_record(value)
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, tuple | list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        return {key: to_jsonable(item) for key, item in value.items()}
    if value is None or isinstance(value, bool | int | float | str):
        return value
    raise TypeError(f"unsupported canonical JSON type: {type(value).__name__}")


def decimal_text(value: Fraction) -> str:
    with localcontext() as context:
        context.prec = 50
        decimal_value = Decimal(value.numerator) / Decimal(value.denominator)
        return format(decimal_value.quantize(_DECIMAL_PLACES, rounding=ROUND_HALF_EVEN), "f")


def fraction_record(value: Fraction) -> dict[str, object]:
    return {
        "numerator": value.numerator,
        "denominator": value.denominator,
        "decimal": decimal_text(value),
    }


def canonical_json_bytes(value: object) -> bytes:
    payload = json.dumps(
        to_jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (payload + "\n").encode("utf-8")


def canonical_jsonl_bytes(values: list[object] | tuple[object, ...]) -> bytes:
    return b"".join(canonical_json_bytes(value) for value in values)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_canonical_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(value))
