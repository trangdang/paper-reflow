"""Coverage for the reserved header/footer content-band handling: stamp-side
classification (`_header_footer_stamp`), running-head/footer detection
(`detect_content_bands` + `get_text_blocks` exclusion), and the FIGURE/GRAPHIC
clip that keeps a too-tall drawing bbox from bleeding into the running-head
strip while leaving text elements (which carry the reflow words) untouched."""

import fitz

from lib.config import HEADER_FOOTER_BAND_FRACTION, VERTICAL_PAD_PT
from lib.elements import Bbox, Column, Element, Kind, PageLayout
from workflow.bbox_padding import pad_and_snap_bboxes
from workflow.content_bands import (
    _header_footer_stamp,
    _norm_running,
    detect_content_bands,
    get_text_blocks,
)

PAGE_W = 600.0
PAGE_H = 800.0
BAND = HEADER_FOOTER_BAND_FRACTION * PAGE_H  # top/bottom band edge


def test_norm_running_drops_digits_and_punctuation():
    # Page number and punctuation drop out so a running head matches across
    # pages regardless of its per-page page number...
    assert _norm_running("Glauz and Harwood 7") == "glauzandharwood"
    assert _norm_running("Glauz and Harwood") == "glauzandharwood"
    # ...and a copyright line reduces to a stable core across pages.
    assert _norm_running("0-7803-9521-2/06/$20.00 §2006 IEEE.784") == "ieee"
    # A pure page number (or digit-ish math fragment) has no letters.
    assert _norm_running("12") == ""
    assert _norm_running("⌧!0") == ""


# --- Running-head/footer detection over small in-memory documents ---

DOC_H = 800.0


def _doc(page_specs):
    """Build an in-memory PDF. Each page spec is a list of (x, baseline_y, text)
    tuples inserted as separate text blocks."""
    doc = fitz.open()
    for spec in page_specs:
        page = doc.new_page(width=PAGE_W, height=DOC_H)
        for x, y, text in spec:
            page.insert_text((x, y), text, fontsize=9)
    return doc


def _kept_texts(page, band):
    blocks, _ = get_text_blocks(page, band)
    return ["".join(s["text"] for line in b["lines"] for s in line["spans"]) for b in blocks]


def test_recurring_header_and_footer_define_band_and_are_excluded():
    header = (50, 30, "Recurring Journal Name")
    footer = (50, 775, "Copyright Boilerplate Line")
    body = lambda i: (50, 400, f"Body words unique to page {i}")  # noqa: E731
    doc = _doc([[header, footer, body(i)] for i in range(4)])

    band = detect_content_bands(doc)
    header_bottom, footer_top = band
    # Header band reaches below the running head; footer band above the footer.
    assert 20 < header_bottom < 60
    assert DOC_H - 60 < footer_top < DOC_H - 20

    kept = _kept_texts(doc[1], band)
    assert any("Body words" in t for t in kept)
    assert not any("Recurring Journal" in t for t in kept)
    assert not any("Copyright Boilerplate" in t for t in kept)


def test_one_off_top_text_does_not_create_header_band():
    # A block that appears near the top of only one page (e.g. a paper title on
    # the first page, or a stray math fragment) must NOT be read as a running
    # head -- recurrence, not position, is the signal. With no recurring header
    # the band stays open at the top and the text is kept.
    doc = _doc(
        [
            [(50, 30, "Only On First Page Title"), (50, 400, "alpha body one")],
            [(50, 400, "bravo body two")],
            [(50, 400, "charlie body three")],
        ]
    )
    band = detect_content_bands(doc)
    assert band[0] == 0.0  # header edge stays at the top
    kept = _kept_texts(doc[0], band)
    assert any("Only On First Page Title" in t for t in kept)


def test_footerless_bottom_digit_fragment_does_not_invent_footer():
    # A small digit-bearing math fragment near the bottom of a page must not be
    # mistaken for a page-number stamp when there's no recurring footer -- that
    # would pull the footer band up over real body content. (Regression for the
    # micro_lie_longer '⌧!0' bottom fragment.)
    doc = _doc(
        [
            [(50, 400, "alpha"), (120, 775, "⌧!0")],
            [(50, 400, "bravo")],
            [(50, 400, "charlie")],
        ]
    )
    band = detect_content_bands(doc)
    assert band[1] == DOC_H  # footer edge stays at the page bottom


def test_stamp_side_classification():
    rect = fitz.Rect(0, 0, PAGE_W, PAGE_H)
    # Small block in the top band -> header.
    assert _header_footer_stamp(Bbox(560, 10, 580, 22), rect) == "header"
    # Small block in the bottom band -> footer.
    assert _header_footer_stamp(Bbox(560, PAGE_H - 15, 580, PAGE_H - 3), rect) == "footer"
    # Small block in the middle of the page -> not a stamp.
    assert _header_footer_stamp(Bbox(560, 400, 580, 412), rect) is None
    # Wide running head in the top band -> too wide to be a stamp (kept as body).
    assert _header_footer_stamp(Bbox(40, 10, 500, 22), rect) is None


def _layout(elements, band):
    return PageLayout(
        page_no=1,
        left_col_x=(50.0, 290.0),
        right_col_x=(310.0, 550.0),
        gutter_x=(290.0, 310.0),
        is_two_column=True,
        elements=elements,
        content_band=band,
    )


def test_pad_clamps_figure_but_not_text_into_band():
    band = (BAND, PAGE_H)  # header strip is [0, BAND)
    # A LEFT-column figure whose tight top was already trimmed to the band edge.
    fig = Element(kind=Kind.FIGURE, page_no=1, column=Column.LEFT, bbox=Bbox(60, BAND, 280, 400))
    # A text element straddling the band edge (e.g. a running head merged into
    # the first body paragraph) must keep its full padded extent.
    para = Element(
        kind=Kind.PARAGRAPH, page_no=1, column=Column.LEFT, bbox=Bbox(60, BAND - 15, 280, 500)
    )
    layout = _layout([fig, para], band)
    pad_and_snap_bboxes(layout, fitz.Rect(0, 0, PAGE_W, PAGE_H))

    # Figure padding is clamped at the band edge, never above it.
    assert fig.padded_bbox.y0 == BAND
    # Paragraph keeps its normal upward padding, poking above the band edge.
    assert para.padded_bbox.y0 == (BAND - 15) - VERTICAL_PAD_PT
