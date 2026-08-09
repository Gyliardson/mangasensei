# Give My Regards to Black Jack Test Fixtures

This directory contains a small corpus of authorized, real manga pages from *Give My Regards to Black Jack* (ブラックジャックによろしく) by SHUHO SATO (佐藤秀峰). 

## Rationale
MangaSensei maintains these selected images to validate OCR regressions and edge cases with real-world complexities such as varied dialogue baseline direction, heavy graphical elements, very short texts, and mixed environmental text. This ensures robust testing compared to synthetic inputs alone. 

**Note on Integration:** This corpus is currently provided as a testing infrastructure only. The automated OCR test pipeline ("Real OCR Smoke") currently runs deterministic synthetic images until a separate task integrates these fixtures.

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
- File integrity and metadata for each fixture can be verified using `manifest.json`.

## Adding New Pages
If you need to add new fixtures to this corpus, follow these rules:
1. **Source:** Only add data distributed officially.
2. **Terms:** Review the terms again at [https://densho810.com/free/](https://densho810.com/free/) and verify that no third-party rights prevent usage.
3. **Documentation:** Document the provenance accurately.
4. **Integrity:** Calculate the SHA-256 hashes manually and update the `manifest.json`.
5. **Purpose:** Justify the test purpose of each new fixture to avoid redundant pages.
