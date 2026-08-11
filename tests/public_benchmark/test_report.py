from __future__ import annotations

from pathlib import Path

from scripts.public_benchmark.contracts import (
    BBox,
    CorpusBundle,
    GroundTruthPage,
    GroundTruthRegion,
    NegativeZone,
    Observation,
    ObservationPage,
    ObservedRegion,
)
from scripts.public_benchmark.report import build_report, serialize_report

SHA40 = "1" * 40
SHA64 = "2" * 64


def make_gt(
    region_id: str,
    bbox: BBox,
    *,
    text: str = "字",
    form: str = "base",
    detection: bool = True,
    recognition: bool = True,
    order: int | None = 0,
) -> GroundTruthRegion:
    return GroundTruthRegion(
        id=region_id,
        bbox=bbox,
        polygon=(
            (bbox.x, bbox.y),
            (bbox.x + bbox.width, bbox.y),
            (bbox.x + bbox.width, bbox.y + bbox.height),
        ),
        transcription_raw=text,
        text_role="dialogue",
        text_form=form,
        detection_scored=detection,
        recognition_scored=recognition,
        reading_order_scored=order is not None,
        reading_order_position=order,
    )


def make_obs(
    region_id: str,
    bbox: BBox,
    *,
    text: str = "字",
    order: int = 0,
) -> ObservedRegion:
    return ObservedRegion(
        id=region_id,
        bbox=bbox,
        polygon=(
            (bbox.x, bbox.y),
            (bbox.x + bbox.width, bbox.y),
            (bbox.x + bbox.width, bbox.y + bbox.height),
        ),
        angle=0.0,
        confidence=0.9,
        text=text,
        reading_order=order,
    )


def score(
    ground_truth: tuple[GroundTruthRegion, ...],
    observations: tuple[ObservedRegion, ...],
    *,
    negative_zones: tuple[NegativeZone, ...] = (),
) -> dict[str, object]:
    ordered = tuple(
        region.id
        for region in sorted(
            (item for item in ground_truth if item.reading_order_scored),
            key=lambda item: (
                item.reading_order_position if item.reading_order_position is not None else -1
            ),
        )
    )
    page = GroundTruthPage(
        id="page",
        width=100,
        height=100,
        image_sha256=SHA64,
        annotation_sha256="3" * 64,
        regions=ground_truth,
        furigana_relation_ids=(),
        presentation_mark_ids=(),
        negative_zones=negative_zones,
        reading_order_sequence=ordered,
    )
    corpus = CorpusBundle(
        root=Path("."),
        corpus_id="corpus",
        schema_version=1,
        manifest_sha256="4" * 64,
        annotation_schema_sha256="5" * 64,
        pages=(page,),
    )
    observation_page = ObservationPage(
        id="page",
        image_sha256=SHA64,
        annotation_sha256="3" * 64,
        width=100,
        height=100,
        regions=observations,
    )
    observation = Observation(
        path=Path("observation.json"),
        sha256="6" * 64,
        schema_version="1.0.0",
        kind="mangasensei-public-ocr-observation",
        corpus_id="corpus",
        corpus_schema_version=1,
        manifest_sha256="4" * 64,
        annotation_schema_sha256="5" * 64,
        producer={"repositorySha": SHA40},
        features={},
        ocr={},
        runtime={},
        pages=(observation_page,),
    )
    return build_report(corpus, observation, evaluator_repository_sha=SHA40)


def page_report(report: dict[str, object]) -> dict[str, object]:
    pages = report["pages"]
    assert isinstance(pages, list)
    value = pages[0]
    assert isinstance(value, dict)
    return value


def counts(report: dict[str, object]) -> dict[str, int]:
    aggregate = report["aggregate"]
    assert isinstance(aggregate, dict)
    detection = aggregate["detection"]
    assert isinstance(detection, dict)
    result = detection["counts"]
    assert isinstance(result, dict)
    return result


def test_perfect_missing_and_extra_detection() -> None:
    gt = (make_gt("g", BBox(10, 10, 10, 10)),)
    perfect = score(gt, (make_obs("o", BBox(10, 10, 10, 10)),))
    assert counts(perfect) == {"truePositive": 1, "falsePositive": 0, "falseNegative": 0}

    missing = score(gt, ())
    assert counts(missing) == {"truePositive": 0, "falsePositive": 0, "falseNegative": 1}

    extra = score(
        gt,
        (
            make_obs("o", BBox(10, 10, 10, 10), order=0),
            make_obs("x", BBox(50, 50, 10, 10), order=1),
        ),
    )
    assert counts(extra) == {"truePositive": 1, "falsePositive": 1, "falseNegative": 0}


def test_duplicate_split_and_merged_detection_have_one_to_one_consequences() -> None:
    gt = (make_gt("g", BBox(10, 10, 10, 10)),)
    duplicate = score(
        gt,
        (
            make_obs("o1", BBox(10, 10, 10, 10), order=0),
            make_obs("o2", BBox(10, 10, 10, 10), order=1),
        ),
    )
    assert counts(duplicate) == {"truePositive": 1, "falsePositive": 1, "falseNegative": 0}

    split = score(
        gt,
        (
            make_obs("left", BBox(10, 10, 5, 10), order=0),
            make_obs("right", BBox(15, 10, 5, 10), order=1),
        ),
    )
    assert counts(split) == {"truePositive": 1, "falsePositive": 1, "falseNegative": 0}

    merged_gt = (
        make_gt("g1", BBox(10, 10, 5, 10), order=0),
        make_gt("g2", BBox(15, 10, 5, 10), order=1),
    )
    merged = score(merged_gt, (make_obs("merged", BBox(10, 10, 10, 10)),))
    assert counts(merged) == {"truePositive": 1, "falsePositive": 0, "falseNegative": 1}


def test_strict_nfc_recognition_differences() -> None:
    one_char = score(
        (make_gt("g", BBox(0, 0, 10, 10), text="猫"),),
        (make_obs("o", BBox(0, 0, 10, 10), text="犬"),),
    )
    region = page_report(one_char)["recognition"]
    assert isinstance(region, dict)
    per_region = region["perRegion"]
    assert isinstance(per_region, list)
    assert per_region[0]["editDistance"] == 1

    width = score(
        (make_gt("g", BBox(0, 0, 10, 10), text="７番線"),),
        (make_obs("o", BBox(0, 0, 10, 10), text="7番線"),),
    )
    width_region = page_report(width)["recognition"]
    assert isinstance(width_region, dict)
    assert width_region["perRegion"][0]["exactMatch"] is False

    kana = score(
        (make_gt("g", BBox(0, 0, 10, 10), text="かな"),),
        (make_obs("o", BBox(0, 0, 10, 10), text="カナ"),),
    )
    kana_region = page_report(kana)["recognition"]
    assert isinstance(kana_region, dict)
    assert kana_region["perRegion"][0]["exactMatch"] is False

    nfc = score(
        (make_gt("g", BBox(0, 0, 10, 10), text="が"),),
        (make_obs("o", BBox(0, 0, 10, 10), text="か\u3099"),),
    )
    nfc_region = page_report(nfc)["recognition"]
    assert isinstance(nfc_region, dict)
    item = nfc_region["perRegion"][0]
    assert item["rawExactlyEqual"] is False
    assert item["exactMatch"] is True
    assert item["editDistance"] == 0


def test_reading_order_inversion_missing_and_extra_observation() -> None:
    ground_truth = (
        make_gt("g1", BBox(0, 0, 10, 10), order=0),
        make_gt("g2", BBox(20, 0, 10, 10), order=1),
    )
    inverted = score(
        ground_truth,
        (
            make_obs("o1", BBox(0, 0, 10, 10), order=1),
            make_obs("o2", BBox(20, 0, 10, 10), order=0),
        ),
    )
    reading = page_report(inverted)["readingOrder"]
    assert isinstance(reading, dict)
    assert reading["comparablePairCount"] == 1
    assert reading["inversionCount"] == 1
    assert reading["pairwiseOrderingAccuracyOnMatchedRegions"]["decimal"] == "0.000000"
    assert reading["exactSequence"]["matches"] is False

    missing = score(ground_truth, (make_obs("o1", BBox(0, 0, 10, 10), order=0),))
    missing_reading = page_report(missing)["readingOrder"]
    assert isinstance(missing_reading, dict)
    assert missing_reading["exactSequence"] == {
        "status": "not-measured",
        "reason": "incomplete-region-matching",
        "matches": None,
    }

    with_extra = score(
        ground_truth,
        (
            make_obs("o1", BBox(0, 0, 10, 10), order=0),
            make_obs("extra", BBox(50, 50, 10, 10), order=1),
            make_obs("o2", BBox(20, 0, 10, 10), order=2),
        ),
    )
    extra_reading = page_report(with_extra)["readingOrder"]
    assert isinstance(extra_reading, dict)
    assert extra_reading["inversionCount"] == 0
    assert extra_reading["exactSequence"]["matches"] is True
    assert counts(with_extra)["falsePositive"] == 1


def test_negative_zone_intrusion_and_duplicate_hits() -> None:
    zone_box = BBox(40, 40, 10, 10)
    zone = NegativeZone(
        id="n1",
        kind="graphic-text-confusable",
        bbox=zone_box,
        polygon=((40, 40), (50, 40), (50, 50)),
    )
    report = score(
        (),
        (
            make_obs("o1", BBox(40, 40, 5, 10), order=0),
            make_obs("o2", BBox(40, 40, 5, 10), order=1),
        ),
        negative_zones=(zone,),
    )
    negative = page_report(report)["negativeZones"]
    assert isinstance(negative, dict)
    assert negative["zonesTotal"] == 1
    assert negative["zonesHit"] == 1
    assert negative["hitPairCount"] == 2
    assert negative["uniqueObservedRegionsHittingZones"] == 2


def test_detection_unscored_positive_ignores_match_and_inside_unmatched_observation() -> None:
    unscored = (make_gt("g", BBox(10, 10, 10, 10), detection=False, recognition=False, order=None),)
    matched = score(unscored, (make_obs("o", BBox(10, 10, 10, 10)),))
    assert counts(matched) == {"truePositive": 0, "falsePositive": 0, "falseNegative": 0}
    ignored = page_report(matched)["ignoredObservations"]
    assert isinstance(ignored, list)
    assert ignored[0]["reason"] == "matched_detection_unscored_ground_truth"

    below_iou_but_inside = score(unscored, (make_obs("o", BBox(10, 10, 4, 10)),))
    assert counts(below_iou_but_inside) == {
        "truePositive": 0,
        "falsePositive": 0,
        "falseNegative": 0,
    }
    ignored = page_report(below_iou_but_inside)["ignoredObservations"]
    assert isinstance(ignored, list)
    assert ignored[0]["reason"] == "ignored_unscored_ground_truth"


def test_recognition_unscored_and_ruby_slice() -> None:
    unscored = score(
        (make_gt("g", BBox(0, 0, 10, 10), recognition=False),),
        (make_obs("o", BBox(0, 0, 10, 10)),),
    )
    recognition = page_report(unscored)["recognition"]
    assert isinstance(recognition, dict)
    assert recognition["slices"]["all"]["groundTruthTotal"] == 0

    ruby = score(
        (make_gt("ruby", BBox(0, 0, 10, 10), text="かな", form="ruby", order=None),),
        (make_obs("o", BBox(0, 0, 10, 10), text="かな"),),
    )
    ruby_recognition = page_report(ruby)["recognition"]
    assert isinstance(ruby_recognition, dict)
    assert ruby_recognition["slices"]["ruby"]["matched"] == 1
    assert ruby_recognition["slices"]["base"]["coverage"]["status"] == "not-applicable"


def test_unsupported_metric_families_are_explicit_not_zeroes() -> None:
    report = score((), ())
    unsupported = report["unsupportedMetricFamilies"]
    assert isinstance(unsupported, list)
    assert {item["family"] for item in unsupported} == {
        "presentation-marks-boten",
        "furigana-relationship",
        "text-role-classification",
        "linguistics-jmdict",
    }
    assert all(item["status"] == "unsupported" for item in unsupported)


def test_zero_denominators_have_explicit_status_and_serialization_is_byte_identical() -> None:
    report = score((), ())
    aggregate = report["aggregate"]
    assert isinstance(aggregate, dict)
    detection = aggregate["detection"]
    assert isinstance(detection, dict)
    assert detection["precision"]["status"] == "insufficient-data"
    recognition = aggregate["recognition"]
    assert isinstance(recognition, dict)
    assert recognition["slices"]["all"]["coverage"]["status"] == "not-applicable"
    assert serialize_report(report) == serialize_report(score((), ()))
