# Third-Party Notices

MangaSensei uses third-party software and data sources. Third-party components retain their own licenses and notices.

## JMdict Data

MangaSensei bootstraps local normalized dictionary packs from [`scriptin/jmdict-simplified`](https://github.com/scriptin/jmdict-simplified), which is derived from JMdict data maintained by the Electronic Dictionary Research and Development Group (EDRDG).

The reviewed packs below use the same source snapshot, `jmdict-simplified-3.6.2+20260803141815`. English is the default deterministic dictionary pack. German is an additional explicitly selectable pack. The generated normalized files are local runtime data and are not committed to Git or included in the Docker image.

| Product language | Upstream language | Source asset | Source SHA-256 | Source bytes | Normalized file | Normalized SHA-256 | Normalized bytes | Entries |
| --- | --- | --- | --- | ---: | --- | --- | ---: | ---: |
| `en` | `eng` | `jmdict-eng-3.6.2+20260803141815.json.zip` | `1806d2817215ebe7ded997c8dac4831a3335d83ed12f321ac869a97e745d3a5c` | `11475140` | `jmdict.json` | `93026b2540d40e9175a11d9b770e77b21ef6be5daf136cee680fa550c62193dc` | `65872497` | `218290` |
| `de` | `ger` | `jmdict-ger-3.6.2+20260803141815.json.zip` | `4da33c567bb03490ffc9819fd1b3e8efc6522a4a790c99b0d2677094f184b7b3` | `7014092` | `jmdict-de.json` | `d9ee60df9ab892c91b3e20f2d3a55e4bc87d74884b7776fde957eea2c2f05e0f` | `42382199` | `128931` |

Both reviewed manifests declare:

- license ID: `CC-BY-SA-4.0`;
- attribution: JMdict data provided by the Electronic Dictionary Research and Development Group (EDRDG);
- redistribution status: `local-bootstrap-derived-data`.

The pack registry and per-language manifests are tracked under
[`backend/src/mangasensei/linguistics/`](backend/src/mangasensei/linguistics/). The exact source URLs, compressed-size bounds, maximum accepted uncompressed size, converter version and independently verified normalized metadata are authoritative there.

There is no reviewed word-level Portuguese JMdict pack in this contract. Portuguese KANJIDIC data is not used as a substitute for word-level JMdict vocabulary.

References:

- [EDRDG license](https://www.edrdg.org/edrdg/licence.html)
- [EDRDG attribution samples](https://www.edrdg.org/edrdg/sample.html)
- [jmdict-simplified project](https://github.com/scriptin/jmdict-simplified)

## Manga Image Translator

MangaSensei vendors a minimal OCR subset from [`zyddnys/manga-image-translator`](https://github.com/zyddnys/manga-image-translator) at commit `95227a2bb0fd306cd4f0c104d57284026f991b3a`.

The vendored upstream license is preserved at
[`backend/src/mangasensei/ocr/vendor/manga_image_translator/LICENSE`](backend/src/mangasensei/ocr/vendor/manga_image_translator/LICENSE).

MangaSensei-specific modifications include dependency narrowing, eager-import reduction, fixed manifest verification, restricted PyTorch checkpoint loading, and CPU-oriented runtime integration.

## OCR Model Weights

OCR model artifacts are downloaded locally through `mangasensei models download` and verified against
[`backend/src/mangasensei/ocr/models/manifest.json`](backend/src/mangasensei/ocr/models/manifest.json).

The artifacts are intentionally excluded from Git and Docker images pending rights review. The application requires local verification before loading them.

## Python And JavaScript Dependencies

Runtime and development dependencies are pinned in [`uv.lock`](uv.lock) and
[`package-lock.json`](package-lock.json). Their licenses are not reproduced in this
notice file; consult each upstream package distribution for its license terms.

### OpenCV Python Headless

The local OCR runtime uses the headless OpenCV Python wheel pinned in `uv.lock`. The `opencv-python` packaging scripts are MIT-licensed, OpenCV itself is Apache-2.0, and the binary wheels include additional third-party components documented by the upstream distribution. The headless package is used because MangaSensei does not require OpenCV GUI functions in its server or worker runtime.

References:

- [OpenCV Python packaging and licenses](https://github.com/opencv/opencv-python)
- [OpenCV license](https://github.com/opencv/opencv/blob/5.0.0/LICENSE)

## Give My Regards to Black Jack Test Fixtures

The MangaSensei repository contains a small testing corpus of selected pages from the official PDF of Volume 1 of `ブラックジャックによろしく` by `佐藤秀峰` (`Give My Regards to Black Jack` by `SHUHO SATO`).

These fixtures are located at [`tests/fixtures/ocr/real_manga/black_jack/`](tests/fixtures/ocr/real_manga/black_jack/).

They are redistributed according to the specific terms for secondary use published by Sato Manga Works Ltd. at the official source: [https://densho810.com/free/](https://densho810.com/free/).

These files are NOT part of the MangaSensei GPL license and remain subject to the copyright holder's terms. The applicable terms require a post-publication reporting communication to Sato Manga Works after publication/distribution of the files.
