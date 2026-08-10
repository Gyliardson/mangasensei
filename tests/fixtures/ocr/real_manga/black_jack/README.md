# Give My Regards to Black Jack Test Fixtures

This directory contains a small corpus of authorized, real manga pages from *Give My Regards to Black Jack* (ブラックジャックによろしく) by SHUHO SATO (佐藤秀峰).

## Rationale

MangaSensei maintains these selected images to validate OCR regressions and edge cases with real-world complexities such as varied dialogue baseline direction, heavy graphical elements, very short texts, and mixed environmental text. This complements deterministic synthetic coverage without turning full-page OCR output into a brittle snapshot contract.

## Automated assurance tiers

The corpus is actively used by the `OCR Smoke` workflow:

- **PR / `main` OCR gate:** pages 73 and 90 protect reviewed short vertical text targets (`うむ` and the `はい` core of `はい‼`) using broad target areas and region-count bounds. Page 9 protects both the narrow recognizer-batch boundary and a deterministically resampled three-column crop-edge contract. Page 171 protects a second real-source recognizer-context case, while page 201 rejects reviewed necktie texture at the final OCR boundary.
- **Scheduled / release / deep manual gate:** pages 73 and 90 repeat their full reviewed short-text inference three times. Page 9 repeats its narrow recognizer/merge boundary three times, retains its production and resampled crop-edge checks, and page 171 retains its independent context anchor. The remaining seven manifest pages are processed once with a broad catastrophic region-count guard; page 201 also retains its explicit precision assertion. These wider characterization pages do not claim transcript-level ground truth.
- **Fast deterministic CI:** a manifest contract verifies that the complete committed JPG inventory exactly matches `manifest.json`, including SHA-256, dimensions and decoder validity. No OCR models are loaded for this check.

The wider pages intentionally remain characterization cases until a stable, reviewable assertion is established for a specific regression. In particular, page 66 is a candidate for a future real-fixture reading-order relationship, but no semantic ordering claim is encoded merely because the fixture exists.

OpenCV migration probes write controlled detector maps, recognizer crops and OCR sidecars only below ignored `var/ocr-opencv-ab/`. Those generated derivatives are not fixtures, must not be committed, and remain subject to the same terms and handling requirements described below.

## Provenance and Attribution

- **Work:** ブラックジャックによろしく (Give My Regards to Black Jack)
- **Author:** 佐藤秀峰 (SHUHO SATO)
- **Official Source:** [https://densho810.com/free/](https://densho810.com/free/)
- **Original Archive:** The images were extracted directly from the official PDF of Volume 1 distributed through the site above.

## Terms of Use

The pages in this directory are redistributed according to the specific terms for secondary use published by Sato Manga Works Ltd. (有限会社佐藤漫画製作所) at [https://densho810.com/free/](https://densho810.com/free/).

> [!WARNING]
> **These files are NOT covered by the MangaSensei GPL license.** They remain subject to the copyright holder's terms.

> [!IMPORTANT]
> The applicable terms require post-publication reporting to Sato Manga Works (info@densho810.com) when this corpus is published/merged into the repository.

The full ZIP and PDF archives are not versioned in this repository to save space and focus exclusively on the validated test fixtures.

## File Naming and Integrity

- `pdfNNN` in the filenames refers to the exact page number of the source PDF used in the selection, **not** the editorial/printed numbering of the manga.
- `manifest.json` is the canonical fixture inventory. Fast CI verifies every committed JPG against its recorded path, SHA-256 and dimensions and rejects unmanifested JPG files.

## Adding New Pages

If you need to add new fixtures to this corpus, follow these rules:

1. **Source:** Only add data distributed officially.
2. **Terms:** Review the terms again at [https://densho810.com/free/](https://densho810.com/free/) and verify that no third-party rights prevent usage.
3. **Documentation:** Document the provenance accurately.
4. **Integrity:** Calculate the SHA-256 hashes and update `manifest.json`; CI must validate the resulting inventory.
5. **Purpose:** Justify the test purpose of each new fixture to avoid redundant pages.
6. **Assertions:** Do not add transcript, geometry or reading-order ground truth until that specific relationship has been reviewed for stability.
