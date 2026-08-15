from pathlib import Path

path = Path("tests/reading_order_v2/test_heldout_fixture_orientation.py")
text = path.read_text(encoding="utf-8")
old = (
    "from scripts.reading_order_v2.contracts import PAGE_IDS\n"
    "from mangasensei.ocr.reading_order import _partition_manga_tiers  # noqa: PLC2701\n"
    "from scripts.reading_order_v2.fixtures import load_textblock_regions\n"
)
new = (
    "from scripts.reading_order_v2.contracts import PAGE_IDS\n"
    "from scripts.reading_order_v2.fixtures import load_textblock_regions\n\n"
    "from mangasensei.ocr.reading_order import _partition_manga_tiers  # noqa: PLC2701\n"
)
if old not in text:
    raise SystemExit("expected generated import block not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
