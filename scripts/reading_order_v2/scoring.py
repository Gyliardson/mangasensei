from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from collections.abc import Iterable, Mapping, Sequence

from .canonical import fraction_record
from .contracts import AnnotationPage, QualificationPair, REQUIRED_PAIR_SLICES


class ReadingOrderScoreError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PairScore:
    pair_id: str
    page_id: str
    before_region_id: str
    after_region_id: str
    slices: tuple[str, ...]
    inverted: bool

    @property
    def canonical_key(self) -> str:
        return f"{self.page_id}:{self.before_region_id}>{self.after_region_id}"


@dataclass(frozen=True, slots=True)
class SliceScore:
    name: str
    comparable_pair_count: int
    inversion_count: int
    wrong_pairs: tuple[str, ...]

    @property
    def correct_pair_count(self) -> int:
        return self.comparable_pair_count - self.inversion_count

    @property
    def pairwise_accuracy(self) -> Fraction:
        if self.comparable_pair_count == 0:
            return Fraction(0, 1)
        return Fraction(self.correct_pair_count, self.comparable_pair_count)

    @property
    def normalized_inversion_distance(self) -> Fraction:
        if self.comparable_pair_count == 0:
            return Fraction(0, 1)
        return Fraction(self.inversion_count, self.comparable_pair_count)


@dataclass(frozen=True, slots=True)
class PageScore:
    page_id: str
    scored_region_count: int
    observed_scored_region_count: int
    comparable_pair_count: int
    inversion_count: int
    wrong_pairs: tuple[str, ...]
    exact_sequence: bool
    pair_scores: tuple[PairScore, ...]

    @property
    def coverage(self) -> Fraction:
        if self.scored_region_count == 0:
            return Fraction(1, 1)
        return Fraction(self.observed_scored_region_count, self.scored_region_count)

    @property
    def pairwise_accuracy(self) -> Fraction:
        if self.comparable_pair_count == 0:
            return Fraction(1, 1)
        return Fraction(
            self.comparable_pair_count - self.inversion_count,
            self.comparable_pair_count,
        )

    @property
    def normalized_inversion_distance(self) -> Fraction:
        if self.comparable_pair_count == 0:
            return Fraction(0, 1)
        return Fraction(self.inversion_count, self.comparable_pair_count)


@dataclass(frozen=True, slots=True)
class ReadingOrderScores:
    pages: tuple[PageScore, ...]
    slices: tuple[SliceScore, ...]

    def slice(self, name: str) -> SliceScore:
        for item in self.slices:
            if item.name == name:
                return item
        raise KeyError(name)

    @property
    def wrong_pairs(self) -> frozenset[str]:
        return frozenset(self.slice("aggregate").wrong_pairs)


def _validate_observed(annotation: AnnotationPage, final_order: Sequence[str]) -> tuple[str, ...]:
    if len(final_order) != len(set(final_order)):
        raise ReadingOrderScoreError(f"{annotation.page_id}: duplicate observed region ID")
    known = set(annotation.known_region_ids)
    unexpected = sorted(set(final_order) - known)
    if unexpected:
        raise ReadingOrderScoreError(
            f"{annotation.page_id}: unexpected observed region IDs {unexpected!r}"
        )
    scored_ids = tuple(region.region_id for region in annotation.regions if region.scored)
    missing = sorted(set(scored_ids) - set(final_order))
    if missing:
        raise ReadingOrderScoreError(f"{annotation.page_id}: missing scored region IDs {missing!r}")
    scored = set(scored_ids)
    return tuple(region_id for region_id in final_order if region_id in scored)


def _all_gt_pairs(annotation: AnnotationPage) -> tuple[tuple[str, str], ...]:
    sequence = annotation.reading_order_sequence
    return tuple(
        (sequence[left], sequence[right])
        for left in range(len(sequence))
        for right in range(left + 1, len(sequence))
    )


def _qualification_map(annotation: AnnotationPage) -> dict[tuple[str, str], QualificationPair]:
    return {
        (pair.before_region_id, pair.after_region_id): pair
        for pair in annotation.qualification_pairs
    }


def score_page(annotation: AnnotationPage, final_order: Sequence[str]) -> PageScore:
    scored_observed = _validate_observed(annotation, final_order)
    position = {region_id: index for index, region_id in enumerate(scored_observed)}
    qualification = _qualification_map(annotation)
    wrong_pairs: list[str] = []
    pair_scores: list[PairScore] = []
    all_pairs = _all_gt_pairs(annotation)
    for before, after in all_pairs:
        inverted = position[before] > position[after]
        key = f"{annotation.page_id}:{before}>{after}"
        if inverted:
            wrong_pairs.append(key)
        declared = qualification.get((before, after))
        if declared is not None:
            pair_scores.append(
                PairScore(
                    pair_id=declared.pair_id,
                    page_id=annotation.page_id,
                    before_region_id=before,
                    after_region_id=after,
                    slices=declared.slices,
                    inverted=inverted,
                )
            )
    return PageScore(
        page_id=annotation.page_id,
        scored_region_count=len(annotation.reading_order_sequence),
        observed_scored_region_count=len(scored_observed),
        comparable_pair_count=len(all_pairs),
        inversion_count=len(wrong_pairs),
        wrong_pairs=tuple(sorted(wrong_pairs)),
        exact_sequence=scored_observed == annotation.reading_order_sequence,
        pair_scores=tuple(sorted(pair_scores, key=lambda item: item.pair_id)),
    )


def _slice_from_pairs(name: str, pairs: Iterable[PairScore]) -> SliceScore:
    selected = tuple(pairs)
    wrong = tuple(sorted(pair.canonical_key for pair in selected if pair.inverted))
    return SliceScore(
        name=name,
        comparable_pair_count=len(selected),
        inversion_count=len(wrong),
        wrong_pairs=wrong,
    )


def score_corpus(
    annotations: Sequence[AnnotationPage],
    orderings: Mapping[str, Sequence[str]],
) -> ReadingOrderScores:
    annotation_ids = tuple(annotation.page_id for annotation in annotations)
    if len(annotation_ids) != len(set(annotation_ids)):
        raise ReadingOrderScoreError("duplicate annotation page IDs")
    if set(orderings) != set(annotation_ids):
        raise ReadingOrderScoreError(
            f"ordering page set mismatch: missing={sorted(set(annotation_ids) - set(orderings))}, "
            f"extra={sorted(set(orderings) - set(annotation_ids))}"
        )
    pages = tuple(
        score_page(annotation, orderings[annotation.page_id])
        for annotation in sorted(annotations, key=lambda item: item.page_id)
    )
    aggregate_pairs = [
        PairScore(
            pair_id=f"all:{page.page_id}:{before}>{after}",
            page_id=page.page_id,
            before_region_id=before,
            after_region_id=after,
            slices=(),
            inverted=f"{page.page_id}:{before}>{after}" in set(page.wrong_pairs),
        )
        for annotation, page in zip(
            sorted(annotations, key=lambda item: item.page_id), pages, strict=True
        )
        for before, after in _all_gt_pairs(annotation)
    ]
    declared_pairs = [pair for page in pages for pair in page.pair_scores]
    slices = [_slice_from_pairs("aggregate", aggregate_pairs)]
    for name in REQUIRED_PAIR_SLICES:
        slices.append(
            _slice_from_pairs(name, (pair for pair in declared_pairs if name in pair.slices))
        )
    return ReadingOrderScores(pages=pages, slices=tuple(slices))


def wrong_set_comparison(
    control: ReadingOrderScores, candidate: ReadingOrderScores
) -> dict[str, object]:
    control_wrong = control.wrong_pairs
    candidate_wrong = candidate.wrong_pairs
    new_wrong = tuple(sorted(candidate_wrong - control_wrong))
    corrected = tuple(sorted(control_wrong - candidate_wrong))
    return {
        "candidateWrongPairSubsetOfControl": candidate_wrong <= control_wrong,
        "candidateWrongPairStrictSubsetOfControl": candidate_wrong < control_wrong,
        "newWrongPairs": list(new_wrong),
        "correctedWrongPairs": list(corrected),
    }


def score_to_dict(scores: ReadingOrderScores) -> dict[str, object]:
    return {
        "schemaVersion": "reading-order-v2-scores-v1",
        "pages": [
            {
                "pageId": page.page_id,
                "coverage": fraction_record(page.coverage),
                "scoredRegionCount": page.scored_region_count,
                "observedScoredRegionCount": page.observed_scored_region_count,
                "comparablePairCount": page.comparable_pair_count,
                "inversionCount": page.inversion_count,
                "wrongPairs": list(page.wrong_pairs),
                "correctPairCount": page.comparable_pair_count - page.inversion_count,
                "pairwiseAccuracy": fraction_record(page.pairwise_accuracy),
                "normalizedInversionDistance": fraction_record(page.normalized_inversion_distance),
                "exactSequence": page.exact_sequence,
            }
            for page in scores.pages
        ],
        "slices": {
            item.name: {
                "comparablePairCount": item.comparable_pair_count,
                "inversionCount": item.inversion_count,
                "wrongPairs": list(item.wrong_pairs),
                "correctPairCount": item.correct_pair_count,
                "pairwiseAccuracy": fraction_record(item.pairwise_accuracy),
                "normalizedInversionDistance": fraction_record(item.normalized_inversion_distance),
            }
            for item in scores.slices
        },
        "exactSequencePageCount": sum(page.exact_sequence for page in scores.pages),
    }


def _fraction_from_record(value: object, location: str) -> Fraction:
    if not isinstance(value, dict):
        raise ReadingOrderScoreError(f"{location}: expected ratio object")
    numerator = value.get("numerator")
    denominator = value.get("denominator")
    if (
        not isinstance(numerator, int)
        or isinstance(numerator, bool)
        or not isinstance(denominator, int)
        or isinstance(denominator, bool)
        or denominator <= 0
    ):
        raise ReadingOrderScoreError(f"{location}: invalid exact ratio")
    return Fraction(numerator, denominator)


def load_scores(path: "Path") -> ReadingOrderScores:
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schemaVersion") != "reading-order-v2-scores-v1":
        raise ReadingOrderScoreError(f"{path}: wrong score schema")
    page_values = raw.get("pages")
    if not isinstance(page_values, list):
        raise ReadingOrderScoreError(f"{path}: pages must be an array")
    pages: list[PageScore] = []
    for index, value in enumerate(page_values):
        if not isinstance(value, dict):
            raise ReadingOrderScoreError(f"{path}.pages[{index}]: expected object")
        page_id = value.get("pageId")
        wrong_pairs = value.get("wrongPairs")
        if not isinstance(page_id, str) or not isinstance(wrong_pairs, list):
            raise ReadingOrderScoreError(f"{path}.pages[{index}]: malformed page score")
        pages.append(
            PageScore(
                page_id=page_id,
                scored_region_count=int(value["scoredRegionCount"]),
                observed_scored_region_count=int(value["observedScoredRegionCount"]),
                comparable_pair_count=int(value["comparablePairCount"]),
                inversion_count=int(value["inversionCount"]),
                wrong_pairs=tuple(str(item) for item in wrong_pairs),
                exact_sequence=bool(value["exactSequence"]),
                pair_scores=(),
            )
        )
    slice_values = raw.get("slices")
    if not isinstance(slice_values, dict):
        raise ReadingOrderScoreError(f"{path}: slices must be an object")
    slices: list[SliceScore] = []
    for name in ("aggregate", *REQUIRED_PAIR_SLICES):
        value = slice_values.get(name)
        if not isinstance(value, dict):
            raise ReadingOrderScoreError(f"{path}: missing score slice {name}")
        wrong_pairs = value.get("wrongPairs")
        if not isinstance(wrong_pairs, list):
            raise ReadingOrderScoreError(f"{path}: malformed wrong-pair list for {name}")
        score = SliceScore(
            name=name,
            comparable_pair_count=int(value["comparablePairCount"]),
            inversion_count=int(value["inversionCount"]),
            wrong_pairs=tuple(str(item) for item in wrong_pairs),
        )
        if score.pairwise_accuracy != _fraction_from_record(
            value.get("pairwiseAccuracy"), f"{path}.slices.{name}.pairwiseAccuracy"
        ):
            raise ReadingOrderScoreError(f"{path}: inconsistent pairwise accuracy for {name}")
        if score.normalized_inversion_distance != _fraction_from_record(
            value.get("normalizedInversionDistance"),
            f"{path}.slices.{name}.normalizedInversionDistance",
        ):
            raise ReadingOrderScoreError(
                f"{path}: inconsistent normalized inversion distance for {name}"
            )
        slices.append(score)
    return ReadingOrderScores(tuple(pages), tuple(slices))


def _load_ordering_document(path: "Path") -> dict[str, tuple[str, ...]]:
    import json

    raw = json.loads(path.read_text(encoding="utf-8"))
    pages = raw.get("pages")
    if not isinstance(pages, dict) or not all(isinstance(key, str) for key in pages):
        raise ReadingOrderScoreError(f"{path}: ordering pages must be an object")
    result: dict[str, tuple[str, ...]] = {}
    for page_id, value in pages.items():
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ReadingOrderScoreError(f"{path}: malformed ordering for {page_id}")
        result[page_id] = tuple(value)
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    from .canonical import write_canonical_json
    from .contracts import PAGE_IDS, load_annotation
    from .validate_corpus import validate_corpus

    parser = argparse.ArgumentParser(description="Score frozen Reading Order v2 arm output")
    parser.add_argument("--corpus-root", type=Path, required=True)
    parser.add_argument("--ordering", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    validate_corpus(args.corpus_root)
    orderings = _load_ordering_document(args.ordering)
    annotations = tuple(
        load_annotation(args.corpus_root / "annotations" / f"{page_id}.json")
        for page_id in PAGE_IDS
    )
    scores = score_corpus(annotations, orderings)
    write_canonical_json(args.output, score_to_dict(scores))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
