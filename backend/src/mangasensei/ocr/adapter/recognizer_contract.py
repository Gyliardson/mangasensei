"""Output-affecting recognizer constants safe to import without OCR extras."""

RECOGNITION_WARP_VERSION = "inclusive-source-v1"

# Text detectors are optimized for localization and can tightly bound the outer
# glyph strokes. The reviewed 48px recognizer needs a small amount of real page
# context beyond that detector geometry. This is a recognizer-input contract,
# not output geometry: 8% is added on each short-axis side only for recognition.
# The minimum was calibrated on independent licensed pages while preserving the
# detector quadrilateral for merge/final regions.
RECOGNITION_SHORT_EDGE_PAD_RATIO = 0.08
RECOGNITION_SHORT_AXIS_CONTEXT = 1.0 + 2.0 * RECOGNITION_SHORT_EDGE_PAD_RATIO
