from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from scripts.reading_order_post_v2_qualification.v3_clean_room_compat import load_arm_input
from scripts.reading_order_v3_authoring import ContractError
from scripts.reading_order_v3_authoring.contracts import load_input

from ._fixtures import _page

ACCEPTED_ANGLES: tuple[int | float, ...] = (
    0.0,
    -12.5,
    0.125,
    2**53,
    2**53 + 2,
)


def _write_input(tmp_path: Path, angle: object) -> Path:
    payload, _ = _page("angle-page", 0)
    regions = cast(list[dict[str, Any]], payload["regions"])
    regions[0]["angle"] = angle
    path = tmp_path / "input.json"
    path.write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")
    return path


def test_nonrepresentable_integer_is_rejected(tmp_path: Path) -> None:
    path = _write_input(tmp_path, 9007199254740993)
    with pytest.raises(ContractError, match="exactly representable as finite binary64"):
        load_input(path)


def test_exact_binary64_integer_boundary_is_accepted(tmp_path: Path) -> None:
    value = 9007199254740992
    loaded = load_input(_write_input(tmp_path, value))
    assert loaded.regions[0].angle == float(value)
    assert int(loaded.regions[0].angle) == value


def test_representable_integer_above_two_to_the_53_is_accepted(tmp_path: Path) -> None:
    value = 2**53 + 2
    assert value > 2**53
    loaded = load_input(_write_input(tmp_path, value))
    assert loaded.regions[0].angle == float(value)
    assert int(loaded.regions[0].angle) == value


def test_integer_float_overflow_is_contract_error(tmp_path: Path) -> None:
    path = _write_input(tmp_path, 10**400)
    with pytest.raises(ContractError, match="finite binary64 number required"):
        load_input(path)


@pytest.mark.parametrize("angle", [3.5, -12.5, 0.125])
def test_ordinary_finite_floats_remain_accepted(tmp_path: Path, angle: float) -> None:
    loaded = load_input(_write_input(tmp_path, angle))
    assert loaded.regions[0].angle == angle


@pytest.mark.parametrize("angle", [float("nan"), float("inf"), float("-inf"), "nope", True])
def test_nonfinite_nonnumber_and_bool_are_rejected(tmp_path: Path, angle: object) -> None:
    path = _write_input(tmp_path, angle)
    with pytest.raises(ContractError, match="finite binary64 number required"):
        load_input(path)


@pytest.mark.parametrize("angle", ACCEPTED_ANGLES)
def test_every_accepted_sample_maps_exactly_to_runtime_angle(
    tmp_path: Path,
    angle: int | float,
) -> None:
    path = _write_input(tmp_path, angle)
    clean = load_input(path)
    runtime = load_arm_input(path)
    expected = float(angle)
    assert clean.regions[0].angle == expected
    assert runtime.regions[0].angle == clean.regions[0].angle
    if isinstance(angle, int):
        assert int(runtime.regions[0].angle) == angle
