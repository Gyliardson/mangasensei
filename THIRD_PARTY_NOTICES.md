# Third-Party Notices

MangaSensei uses third-party software and data sources. Third-party components retain their own licenses and notices.

## JMdict Data

MangaSensei bootstraps a local normalized dictionary from `scriptin/jmdict-simplified`, which is derived from JMdict data maintained by the Electronic Dictionary Research and Development Group (EDRDG).

The generated `var/data/jmdict.json` file is local runtime data and is not committed to Git or included in the Docker image.

| Item | Value |
| --- | --- |
| Source asset | `jmdict-eng-3.6.2+20260803141815.json.zip` |
| Source URL | `https://github.com/scriptin/jmdict-simplified/releases/download/3.6.2%2B20260803141815/jmdict-eng-3.6.2%2B20260803141815.json.zip` |
| Source SHA-256 | `1806d2817215ebe7ded997c8dac4831a3335d83ed12f321ac869a97e745d3a5c` |
| Normalized SHA-256 | `86721226be551bce297177fcb5a20518d517ff17c00fbaf24c28661d3760a166` |
| Normalized entries | `218290` |
| License | CC BY-SA 4.0 / EDRDG license terms |
| Attribution | JMdict data provided by the Electronic Dictionary Research and Development Group (EDRDG). |

References:

- EDRDG license: `https://www.edrdg.org/edrdg/licence.html`
- EDRDG attribution samples: `https://www.edrdg.org/edrdg/sample.html`
- jmdict-simplified project: `https://github.com/scriptin/jmdict-simplified`

## Manga Image Translator

MangaSensei vendors a minimal OCR subset from `zyddnys/manga-image-translator` at commit `95227a2bb0fd306cd4f0c104d57284026f991b3a`.

The vendored upstream license is preserved at:

`backend/src/mangasensei/ocr/vendor/manga_image_translator/LICENSE`

MangaSensei-specific modifications include dependency narrowing, eager-import reduction, fixed manifest verification, restricted PyTorch checkpoint loading, and CPU-oriented runtime integration.

## OCR Model Weights

OCR model artifacts are downloaded locally through `mangasensei models download` and verified against `backend/src/mangasensei/ocr/models/manifest.json`.

The artifacts are intentionally excluded from Git and Docker images pending rights review. The application requires local verification before loading them.

## Python And JavaScript Dependencies

Runtime and development dependencies are pinned in `uv.lock` and `package-lock.json`. Their licenses are not reproduced in this notice file; consult each upstream package distribution for its license terms.
