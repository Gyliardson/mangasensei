from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from scripts.reading_order_v3_authoring import (
    POSITIVE_FAMILIES,
    SLICE_MINIMA,
    ContractError,
)
from scripts.reading_order_v3_authoring.contracts import (
    PageAnnotation,
    validate_authoring_coverage,
)

from ._fixtures import _annotations, _write


def _without_slice(annotation: PageAnnotation, slice_name: str) -> PageAnnotation:
    pairs = tuple(
        replace(pair, slices=tuple(name for name in pair.slices if name != slice_name))
        for pair in annotation.qualification_pairs
    )
    return replace(annotation, qualification_pairs=pairs)


def test_declared_dedicated_family_without_same_page_pair_slice_rejected(
    tmp_path: Path,
) -> None:
    _write(tmp_path, seal=False)
    design, annotations = _annotations(tmp_path)
    page_id = "page-01"
    family = "c2-gutter-bridge"
    annotations[page_id] = _without_slice(annotations[page_id], family)

    with pytest.raises(ContractError, match="positiveFamilies must exactly match"):
        validate_authoring_coverage(design, annotations)


def test_same_page_positive_pair_slice_omitted_from_design_rejected(tmp_path: Path) -> None:
    _write(tmp_path, seal=False)
    design, annotations = _annotations(tmp_path)
    page_id = "page-01"
    pages = tuple(
        replace(page, positive_families=(), primary_positive_family=None)
        if page.page_id == page_id
        else page
        for page in design.pages
    )
    design = replace(design, pages=pages)

    with pytest.raises(ContractError, match="positiveFamilies must exactly match"):
        validate_authoring_coverage(design, annotations)


def test_exact_binding_valid_fixture_satisfies_dedicated_and_frozen_minima(
    tmp_path: Path,
) -> None:
    _write(tmp_path, seal=False)
    design, annotations = _annotations(tmp_path)
    summary = validate_authoring_coverage(design, annotations)

    assert set(summary.dedicated_positive_pages) == set(POSITIVE_FAMILIES)
    assert summary.dedicated_positive_pages["c2-gutter-bridge"] == ("page-01",)
    for family in POSITIVE_FAMILIES:
        assert summary.dedicated_positive_pages[family]
    for slice_name, minima in SLICE_MINIMA.items():
        assert summary.slice_pair_counts[slice_name] >= minima["minPairs"]
        assert summary.slice_page_counts[slice_name] >= minima["minPages"]


def test_combined_positive_pages_do_not_replace_dedicated_page(tmp_path: Path) -> None:
    _write(tmp_path, seal=False)
    design, annotations = _annotations(tmp_path)
    family = "c1-boundary-positive"
    dedicated_page = "page-00"
    annotations[dedicated_page] = _without_slice(annotations[dedicated_page], family)
    pages = tuple(
        replace(page, positive_families=(), primary_positive_family=None)
        if page.page_id == dedicated_page
        else page
        for page in design.pages
    )
    design = replace(design, pages=pages)

    with pytest.raises(
        ContractError,
        match="missing dedicated positive families.*c1-boundary-positive",
    ):
        validate_authoring_coverage(design, annotations)
