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

### pypdfium2 / bundled PDFium

Hardened local PDF import pins `pypdfium2 5.12.1`. The selected Linux x86_64 wheel contains the Python wrapper, the `pypdfium2_raw` module, and bundled `pypdfium2_raw/libpdfium.so` for PDFium `152.0.7947.0` (build `7947`). The pypdfium2 project is dual-licensed under Apache-2.0 or BSD-3-Clause terms; bundled native components retain their own licenses.

The exact installed wheel was inspected in the Slice D CI gate. Its authoritative native notice bundle is:

`pypdfium2-5.12.1.dist-info/licenses/data/linux_x64/BUILD_LICENSES/`

That directory contains exactly these 16 notice files in the selected wheel:

- `abseil.txt` — Apache License 2.0;
- `agg23.txt` — Anti-Grain Geometry 2.3 permissive notice;
- `fast_float.txt` — MIT License;
- `freetype.txt` — FreeType Project License;
- `icu.txt` — Unicode License v3;
- `lcms.txt` — Little CMS permissive/MIT-style notice;
- `libjpeg_turbo.ijg` and `libjpeg_turbo.md` — libjpeg-turbo/IJG and BSD-style terms documented by the project;
- `libopenjpeg.txt` — BSD 2-Clause License;
- `libpng.txt` — PNG Reference Library License version 2 and historical notices;
- `libtiff.txt` — libtiff permissive notice;
- `llvm-libc.txt` — Apache License 2.0 with LLVM Exceptions;
- `pdfium-binaries.txt` — pdfium-binaries permissive notice;
- `pdfium.txt` — PDFium BSD-style notice;
- `simdutf.txt` — MIT License;
- `zlib.txt` — zlib License.

The complete files in the selected wheel are authoritative; this summary does not replace them. CI archives those exact files as the `pdfium-build-licenses` artifact when validating the PDF import boundary.

Runtime provenance validation rejects a helper version other than `5.12.1`, a PDFium build other than `7947`, a V8/XFA build, a symlinked/missing native library, or a `libpdfium.so` resolved outside the installed `pypdfium2_raw` package directory. The selected x86_64 native library SHA-256 observed by CI is `61c9f745c6296a1050599a99a1ed985036411b591a11bd2a41bafe530ecb4f33`.

`ldd` on that exact `libpdfium.so` resolved only the Linux runtime libraries `libpthread.so.0`, `libm.so.6`, `libgcc_s.so.1`, `libc.so.6`, and the ELF loader; no library was unresolved and no system PDFium was dynamically linked. These runtime libraries are supplied by the pinned Debian/Python base image rather than bundled inside the pypdfium2 wheel.

References:

- [pypdfium2 project](https://github.com/pypdfium2-team/pypdfium2)
- [pypdfium2 licensing documentation](https://pypdfium2.readthedocs.io/en/stable/python_api.html)

### OpenCV Python Headless

The local OCR runtime uses the headless OpenCV Python wheel pinned in `uv.lock`. The `opencv-python` packaging scripts are MIT-licensed, OpenCV itself is Apache-2.0, and the binary wheels include additional third-party components documented by the upstream distribution. The headless package is used because MangaSensei does not require OpenCV GUI functions in its server or worker runtime.

References:

- [OpenCV Python packaging and licenses](https://github.com/opencv/opencv-python)
- [OpenCV license](https://github.com/opencv/opencv/blob/5.0.0/LICENSE)

## MangaSensei Public Demo Corpus

The project-owned [`assets/public-demo/`](assets/public-demo/) corpus is licensed separately under CC BY 4.0. Its SVG artwork, rendered PNG pages and annotations are original MangaSensei corpus assets; this does not change the GPL-3.0-only license of MangaSensei source code or the separate terms of the Black Jack fixtures below.

The deterministic renderer uses Noto Sans CJK JP `Sans2.004` and Noto Serif CJK JP `Serif2.003` from the official [`notofonts/noto-cjk`](https://github.com/notofonts/noto-cjk) repository under SIL OFL 1.1. Font binaries are not committed; exact upstream paths, Git blob IDs, byte sizes and SHA-256 values are recorded in [`assets/public-demo/provenance/fonts.json`](assets/public-demo/provenance/fonts.json) and verified before rendering.

## Give My Regards to Black Jack Test Fixtures

The MangaSensei repository contains a small testing corpus of selected pages from the official PDF of Volume 1 of `ブラックジャックによろしく` by `佐藤秀峰` (`Give My Regards to Black Jack` by `SHUHO SATO`).

These fixtures are located at [`tests/fixtures/ocr/real_manga/black_jack/`](tests/fixtures/ocr/real_manga/black_jack/).

They are redistributed according to the specific terms for secondary use published by Sato Manga Works Ltd. at the official source: [https://densho810.com/free/](https://densho810.com/free/).

These files are NOT part of the MangaSensei GPL license and remain subject to the copyright holder's terms. The applicable terms require a post-publication reporting communication to Sato Manga Works after publication/distribution of the files.
