# Give My Regards to Black Jack Test Fixtures

This directory contains a small **third-party authorized real-manga pressure corpus** from *Give My Regards to Black Jack* (ブラックジャックによろしく) by SHUHO SATO (佐藤秀峰).

The 12 committed fixtures are third-party manga data redistributed under Sato Manga Works' published secondary-use terms. They are not private MangaSensei data, public domain material, project-owned material, CC BY material, or part of MangaSensei's GPL license.

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
- **Official Source:** [Sato Manga Works / Densho Bato free-use distribution](https://densho810.com/free/)
- **Source Volume:** 1
- **Original Archive SHA-256:** `ec7bbb4ce4f719536a2d58f29eb2c665d19b0769303efe009b4d98cccad699e1`
- **Source PDF SHA-256:** `a2ad133db82a21cefce1acccb5548de10d2d74118c1519d86ede8566b02ca8b4`
- **Committed fixture count:** 12
- **Source PDF pages:** 007, 009, 021, 041, 066, 073, 090, 123, 145, 171, 194, 201

The images were extracted directly from the official Volume-1 PDF distributed through the source above. Integrity metadata proves which bytes are committed; permission comes from the copyright holder's terms and is a separate question.

## Rights and Terms of Use

The current official terms were re-reviewed on **2026-08-13**. Subject to their conditions, Sato Manga Works permits reproduction/public transmission, redistribution of its official digital data, commercial and noncommercial secondary use, and adaptations such as cropping, resizing, annotation and other modifications. Promotional/public display is within that broad secondary-use grant.

GitHub-hosted processing and automated OCR are a reasonable application of that broad grant for the official data. The terms do **not** contain a cloud-compute-specific clause; this rights conclusion therefore does not itself enable a Research Lab experiment or weaken any execution/security allowlist.

The permission boundary is specific:

- it applies to official Sato Manga Works digital data for `ブラックジャックによろしく` / `Give My Regards to Black Jack`;
- it does not permit redistribution of scans or digitizations made from physical printed books;
- it does not extend to `新ブラックジャックによろしく` / *The New Give My Regards to Black Jack* or unrelated works;
- Japanese publication must preserve `ブラックジャックによろしく` and `佐藤秀峰`;
- English/non-Japanese publication must preserve `Give My Regards to Black Jack` and `SHUHO SATO`;
- the JASRAC-managed exceptions listed by the holder concern specified pages in Volumes 2, 7 and 11, so they do not affect this selected Volume-1 fixture corpus.

> [!WARNING]
> **These files are NOT covered by the MangaSensei GPL license.** They remain third-party copyrighted material subject to the copyright holder's published terms.

## Post-publication reporting checkpoint

Maintainer attestation: the required post-publication report for the **existing 12-page MangaSensei repository corpus** was sent to Sato Manga Works on **2026-08-13 (BRT / America/Sao_Paulo)**. No sender account details, message headers, Message-ID, screenshots, or private correspondence are retained here.

This checkpoint covers the already published repository fixture use only. A future README/product-demo publication using these images is a **new reportable use**: after it is actually published, review the then-current official terms and send the required post-publication report within their stated deadline. Do not treat this checkpoint as reporting a future demo in advance.

The full ZIP and PDF archives are not versioned in this repository to save space and focus exclusively on the validated test fixtures.

## File Naming and Integrity

- `pdfNNN` in the filenames refers to the exact page number of the source PDF used in the selection, **not** the editorial/printed numbering of the manga.
- `manifest.json` is the canonical fixture inventory. Fast CI verifies every committed JPG against its recorded path, SHA-256 and dimensions and rejects unmanifested JPG files.

## Adding New Pages or Publication Uses

If you need to add fixtures or publish another derived use of this corpus, follow these rules:

1. **Source:** Use only data distributed officially by Sato Manga Works for the covered work; do not substitute a printed-book scan or unofficial scan.
2. **Terms:** Re-review the current [official secondary-use terms](https://densho810.com/free/) and verify that no third-party rights affect the selected source pages/use.
3. **Attribution:** Preserve the holder-required work and author strings for the publication language.
4. **Documentation:** Document provenance and the permission boundary accurately without implying GPL, CC BY, public-domain or project ownership.
5. **Integrity:** For new fixtures, calculate SHA-256 hashes and update `manifest.json`; CI must validate the resulting inventory.
6. **Purpose:** Justify the test purpose of each new fixture to avoid redundant pages.
7. **Assertions:** Do not add transcript, geometry or reading-order ground truth until that specific relationship has been reviewed for stability.
8. **Reporting:** After a new publication/use is actually public, perform the holder-required post-publication report under the then-current terms and record only the minimum durable compliance checkpoint.
