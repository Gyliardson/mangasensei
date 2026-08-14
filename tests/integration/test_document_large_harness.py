from __future__ import annotations

from tests.large_document.generator import generate_pages, workload_manifest


def test_control_plane_max_200_generator_matches_frozen_contract() -> None:
    pages = generate_pages()
    assert workload_manifest(pages)["aggregate"] == {
        "pageCount": 200,
        "pixelCount": 1_920_000,
        "encodedBytes": 39_780,
        "minEncodedBytes": 108,
        "maxEncodedBytes": 201,
        "uniqueImageContents": 200,
        "orderedContentSha256": "c60a3b6c1cf2e2219be89286fc917ccc87d89b2b23e84449f4d83e589b60008b",
    }
    assert pages[0].filename == "page-000001.png"
    assert pages[-1].filename == "page-000200.png"
    assert pages[0].sha256 == "508d5332685fe509cda034237bed46946caab388be9445a6d5d3ed7239654341"
    assert pages[99].sha256 == "a767746637c9098492c82736af1d2d82385a9eeaa7458fac7606872b50202716"
    assert pages[199].sha256 == "b37363a63dc3e9e10759c3fe30ea08b0ab58a626c8f9231b54f2ca8007e025ac"
