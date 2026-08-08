# MangaSensei modification 2026-08-07: keep the vendored package initializer
# inert so importing OCR cannot activate unrelated translation subsystems.

__all__: tuple[str, ...] = ()
