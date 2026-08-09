"""Output-affecting recognizer constants safe to import without OCR extras."""

RECOGNITION_WARP_VERSION = "full-image-context-v1"

# The 48px recognizer's first convolution is 7x7 with padding 3. Reserve that
# radius as real source-image context around detector-tight text lines so edge
# glyph strokes are not surrounded immediately by synthetic CNN zero padding.
RECOGNITION_SHORT_AXIS_PADDING = 3
