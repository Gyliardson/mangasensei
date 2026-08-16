from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from .contracts import PageGroundTruth, QualificationPair

PagePair = tuple[str, str]
WrongPair = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class PairMetrics:
    comparable_pairs: int
    correct_pairs: int
    wrong_pairs_count: int
    pairwise_accuracy: Fraction
    normalized_inversion_distance: Fraction
    normalized_error: Fraction
    wrong_pairs: tuple[WrongPair, ...]


@dataclass(frozen=True, slots=True)
class PageScore:
    page_id: str
    scored_region_count: int
    exact_sequence: bool
    aggregate: PairMetrics
    slices: dict[str, PairMetrics]
    observed_scored_order: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CorpusScore:
    page_count: int
    exact_sequence_pages: int
    aggregate: PairMetrics
    slices: dict[str, PairMetrics]
    pages: tuple[PageScore, ...]


def _pair_metrics(
    page_id: str,
    pairs: list[PagePair],
    observed_position: dict[str, int],
) -> PairMetrics:
    wrong = tuple(
        sorted(
            (page_id, earlier, later)
            for earlier, later in pairs
            if observed_position[earlier] > observed_position[later]
        )
    )
    total = len(pairs)
    wrong_count = len(wrong)
    correct = total - wrong_count
    accuracy = Fraction(correct, total) if total else Fraction(1, 1)
    normalized = Fraction(wrong_count, total) if total else Fraction(0, 1)
    return PairMetrics(
        total,
        correct,
        wrong_count,
        accuracy,
        normalized,
        normalized,
        wrong,
    )


def _all_gt_pairs(sequence: tuple[str, ...]) -> list[PagePair]:
    return [
        (sequence[left], sequence[right])
        for left in range(len(sequence))
        for right in range(left + 1, len(sequence))
    ]


def _slice_pairs(pairs: tuple[QualificationPair, ...]) -> dict[str, list[PagePair]]:
    result: dict[str, list[PagePair]] = {}
    for pair in pairs:
        for slice_name in pair.slices:
            result.setdefault(slice_name, []).append((pair.earlier, pair.later))
    return result


def score_page(gt: PageGroundTruth, observed_order: tuple[str, ...]) -> PageScore:
    if len(set(observed_order)) != len(observed_order):
        raise ValueError(f"{gt.page_id}: observed ordering contains duplicate region IDs")
    expected_all = set(gt.reading_order) | set(gt.unscored_region_ids)
    unexpected = sorted(set(observed_order) - expected_all)
    missing = sorted(expected_all - set(observed_order))
    if unexpected or missing:
        raise ValueError(
            f"{gt.page_id}: observed region set mismatch: "
            f"missing={missing}, unexpected={unexpected}"
        )
    scored_set = set(gt.reading_order)
    scored = tuple(region_id for region_id in observed_order if region_id in scored_set)
    if set(scored) != scored_set:
        raise ValueError(f"{gt.page_id}: scored region set changed")
    position = {region_id: index for index, region_id in enumerate(scored)}
    aggregate = _pair_metrics(gt.page_id, _all_gt_pairs(gt.reading_order), position)
    slices = {
        name: _pair_metrics(gt.page_id, pairs, position)
        for name, pairs in sorted(_slice_pairs(gt.qualification_pairs).items())
    }
    return PageScore(
        page_id=gt.page_id,
        scored_region_count=len(gt.reading_order),
        exact_sequence=scored == gt.reading_order,
        aggregate=aggregate,
        slices=slices,
        observed_scored_order=scored,
    )


def _combine_metrics(metrics: list[PairMetrics]) -> PairMetrics:
    wrong = tuple(sorted(pair for metric in metrics for pair in metric.wrong_pairs))
    total = sum(metric.comparable_pairs for metric in metrics)
    wrong_count = len(wrong)
    correct = total - wrong_count
    accuracy = Fraction(correct, total) if total else Fraction(1, 1)
    normalized = Fraction(wrong_count, total) if total else Fraction(0, 1)
    return PairMetrics(
        total,
        correct,
        wrong_count,
        accuracy,
        normalized,
        normalized,
        wrong,
    )


def score_corpus(page_scores: tuple[PageScore, ...]) -> CorpusScore:
    by_slice: dict[str, list[PairMetrics]] = {}
    for page in page_scores:
        for name, metric in page.slices.items():
            by_slice.setdefault(name, []).append(metric)
    return CorpusScore(
        page_count=len(page_scores),
        exact_sequence_pages=sum(page.exact_sequence for page in page_scores),
        aggregate=_combine_metrics([page.aggregate for page in page_scores]),
        slices={name: _combine_metrics(values) for name, values in sorted(by_slice.items())},
        pages=tuple(sorted(page_scores, key=lambda page: page.page_id)),
    )


def wrong_set_is_subset(control: PairMetrics, candidate: PairMetrics) -> bool:
    return set(candidate.wrong_pairs) <= set(control.wrong_pairs)


def candidate_only_wrong_pairs(
    control: PairMetrics, candidate: PairMetrics
) -> tuple[WrongPair, ...]:
    return tuple(sorted(set(candidate.wrong_pairs) - set(control.wrong_pairs)))


def strict_wrong_set_improvement(control: PairMetrics, candidate: PairMetrics) -> bool:
    return set(candidate.wrong_pairs) < set(control.wrong_pairs)
