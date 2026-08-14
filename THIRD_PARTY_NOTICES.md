# Third-Party Notices

MangaSensei uses third-party software and data sources. Third-party components retain their own licenses and notices.

## JMdict Data

MangaSensei bootstraps one local normalized English dictionary pack from [`scriptin/jmdict-simplified`](https://github.com/scriptin/jmdict-simplified), which is derived from JMdict data maintained by the Electronic Dictionary Research and Development Group (EDRDG).

The reviewed active pack uses source snapshot `jmdict-simplified-3.6.2+20260803141815`. The generated normalized file is local runtime data and is not committed to Git or included in the Docker image.

| Product language | Upstream language | Source asset | Source SHA-256 | Source bytes | Normalized file | Normalized SHA-256 | Normalized bytes | Entries |
| --- | --- | --- | --- | ---: | --- | --- | ---: | ---: |
| `en` | `eng` | `jmdict-eng-3.6.2+20260803141815.json.zip` | `1806d2817215ebe7ded997c8dac4831a3335d83ed12f321ac869a97e745d3a5c` | `11475140` | `jmdict.json` | `93026b2540d40e9175a11d9b770e77b21ef6be5daf136cee680fa550c62193dc` | `65872497` | `218290` |

The reviewed manifest declares:

- license ID: `CC-BY-SA-4.0`;
- attribution: JMdict data provided by the Electronic Dictionary Research and Development Group (EDRDG);
- redistribution status: `local-bootstrap-derived-data`.

The pack registry and English manifest are tracked under
[`backend/src/mangasensei/linguistics/`](backend/src/mangasensei/linguistics/). The exact source URL, compressed-size bounds, maximum accepted uncompressed size, converter version and independently verified normalized metadata are authoritative there.

There is no active reviewed German or word-level Portuguese JMdict pack in this contract. Historical persisted language metadata remains a database compatibility concern and does not require either retired/nonexistent pack to be downloaded. Portuguese KANJIDIC data is not used as a substitute for word-level JMdict vocabulary.

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

Hardened local PDF import pins `pypdfium2 5.12.1` and supports only the reviewed Linux x86_64 and Linux aarch64 binary wheels. Each contains the Python wrapper, the `pypdfium2_raw` module, and bundled `pypdfium2_raw/libpdfium.so` for PDFium `152.0.7947.0` (build `7947`). The pypdfium2 project is dual-licensed under Apache-2.0 or BSD-3-Clause terms; bundled native components retain their own licenses.

The exact installed x86_64 wheel and the reviewed x86_64/aarch64 wheel artifacts are inspected by the PDF Import CI gate. The x86_64 wheel's authoritative native notice bundle is:

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

The complete files in each reviewed wheel are authoritative; this summary does not replace them. CI archives the installed x86_64 bundle plus the license files extracted from both reviewed Linux wheel artifacts as the `pdfium-build-licenses` artifact when validating the PDF import boundary.

Production installation forbids source-building `pypdfium2`. Runtime provenance rejects unsupported OS/architecture, a helper version other than `5.12.1`, a PDFium build other than `7947`, a V8/XFA build, a symlinked/missing/out-of-package native library, or an unexpected native digest. The reviewed native SHA-256 values are Linux x86_64 `61c9f745c6296a1050599a99a1ed985036411b591a11bd2a41bafe530ecb4f33` and Linux aarch64 `f5c8d54a498e2112fbcf53e866c4a5635e9839db3a36d88c4772e5384dabeac6`. CI also verifies the corresponding published wheel SHA-256 values from locked metadata before extracting the native library.

`ldd` on that exact `libpdfium.so` resolves only the Linux runtime libraries `libpthread.so.0`, `libm.so.6`, `libgcc_s.so.1`, `libc.so.6`, and the ELF loader; no library is unresolved and no system PDFium is dynamically linked. These runtime libraries are supplied by the pinned Debian/Python base image rather than bundled inside the pypdfium2 wheel.

References:

- [pypdfium2 project](https://github.com/pypdfium2-team/pypdfium2)
- [pypdfium2 licensing documentation](https://pypdfium2.readthedocs.io/en/stable/python_api.html)

### OpenCV Python Headless

The local OCR runtime uses the headless OpenCV Python wheel pinned in `uv.lock`. The `opencv-python` packaging scripts are MIT-licensed, OpenCV itself is Apache-2.0, and the binary wheels include additional third-party components documented by the upstream distribution. The headless package is used because MangaSensei does not require OpenCV GUI functions in its server or worker runtime.

References:

- [OpenCV Python packaging and licenses](https://github.com/opencv/opencv-python)
- [OpenCV license](https://github.com/opencv/opencv/blob/5.0.0/LICENSE)

## MangaSensei Public Demo Corpus

The project-owned [`assets/public-demo/`](assets/public-demo/) corpus is licensed separately under CC BY 4.0. Its SVG artwork, rendered PNG pages and annotations are original MangaSensei corpus assets; this does not change the GPL-3.0-only license of MangaSensei source code or the separate Sato Manga Works terms governing the Black Jack fixtures below.

The deterministic renderer uses Noto Sans CJK JP `Sans2.004` and Noto Serif CJK JP `Serif2.003` from the official [`notofonts/noto-cjk`](https://github.com/notofonts/noto-cjk) repository under SIL OFL 1.1. Font binaries are not committed; exact upstream paths, Git blob IDs, byte sizes and SHA-256 values are recorded in [`assets/public-demo/provenance/fonts.json`](assets/public-demo/provenance/fonts.json) and verified before rendering.

## Give My Regards to Black Jack Test Fixtures

The MangaSensei repository contains a 12-page **third-party authorized real-manga pressure corpus** selected from the official Volume-1 PDF of `ブラックジャックによろしく` by `佐藤秀峰` (`Give My Regards to Black Jack` by `SHUHO SATO`). The fixtures are located at [`tests/fixtures/ocr/real_manga/black_jack/`](tests/fixtures/ocr/real_manga/black_jack/).

They are third-party manga data redistributed under the [current published secondary-use terms of Sato Manga Works](https://densho810.com/free/), re-reviewed by MangaSensei on **2026-08-13**. Subject to those conditions, the terms permit reproduction/public transmission, redistribution of the copyright holder's official digital data, commercial and noncommercial secondary use, and adaptation. This permission is specific to the official Sato Manga Works data for *Give My Regards to Black Jack*; it does not permit redistribution of scans/digitizations made from physical books, does not extend to `新ブラックジャックによろしく` / *The New Give My Regards to Black Jack*, and does not cover unrelated works.

Required attribution strings are:

- Japanese use: `ブラックジャックによろしく` — `佐藤秀峰`;
- English/non-Japanese use: `Give My Regards to Black Jack` — `SHUHO SATO`.

The holder's listed JASRAC-managed exceptions concern specified pages in Volumes 2, 7 and 11 and therefore do not affect this selected Volume-1 fixture corpus.

The committed corpus provenance records source archive SHA-256 `ec7bbb4ce4f719536a2d58f29eb2c665d19b0769303efe009b4d98cccad699e1` and source PDF SHA-256 `a2ad133db82a21cefce1acccb5548de10d2d74118c1519d86ede8566b02ca8b4`. These integrity values identify the reviewed source bytes; they do not themselves grant permission.

These files are **not** public domain, project-owned, CC BY, unrestricted, or part of the MangaSensei GPL license. They remain third-party copyrighted material subject to the copyright holder's terms.

The broad secondary-use grant reasonably supports automated OCR and GitHub-hosted processing of the official data, but the official terms contain no cloud-compute-specific clause. That rights conclusion does not by itself alter MangaSensei Research Lab allowlists, security controls, model-artifact rights, or execution policy.

### Post-publication reporting checkpoint

Maintainer attestation records that the required post-publication report covering the **existing published 12-page repository fixture use** was sent to Sato Manga Works on **2026-08-13 (BRT / America/Sao_Paulo)**. On **2026-08-14 JST**, Sato Manga Works acknowledged the report, stated that it had reviewed the reported details and URLs, and requested a new report after any future demonstration or other additional use. The acknowledgement is operational/compliance evidence; the published secondary-use terms remain the rights basis. The repository intentionally retains no sender address, mailbox/account data, Message-ID, private email headers, screenshots, or correspondence body for that checkpoint.

A later README/product-demo publication using Black Jack material is a separate new use. After that demo is actually published, the maintainer must review the then-current official terms and perform the required post-publication report within the holder's stated deadline. The 2026-08-13 checkpoint must not be read as advance reporting of a future demo.

See the fixture-level [provenance, integrity and handling contract](tests/fixtures/ocr/real_manga/black_jack/README.md) for the exact inventory and operational rules.
