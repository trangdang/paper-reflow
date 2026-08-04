"""Pure helpers over PyMuPDF text-dict blocks and Bbox: no page object, no
config-driven thresholds. Shared by every per-page detection pass in
workflow/ (content-band detection, gutter detection, figure/caption
matching, build_page_layout itself)."""

from lib.elements import Bbox


def block_bbox(block: dict) -> Bbox:
    return Bbox(*block["bbox"])


def block_text(block: dict) -> str:
    return "".join(s["text"] for line in block["lines"] for s in line["spans"])


def is_rotated_margin_stamp(bbox: Bbox) -> bool:
    """Vertical sidebar text (e.g. an arXiv identifier printed rotated in the
    page margin) shows up as a very narrow, very tall block. It isn't part of
    the normal reading flow, so it's excluded rather than misclassified.

    "Narrow" is judged by aspect ratio, not an absolute width cap alone: a
    longer identifier printed at a larger point size (e.g. the arXiv stamp on
    1812.01537, ~27pt wide and ~341pt tall) overflows a fixed ~25pt width cap
    while still being unmistakably a tall thin sidebar. Requiring the height to
    dwarf the width keeps a genuinely wide-but-tall block (a real narrow
    column) from being mistaken for a stamp."""
    return bbox.height > 100.0 and bbox.width < 35.0 and bbox.height > 5.0 * bbox.width


def is_equation_tag(text: str) -> bool:
    """A parenthesized equation number like '(184)'. When one falls in the
    bottom-margin band at the foot of a column it can look exactly like a
    footer page-number stamp (small, low on the page, digit-bearing); the
    parentheses are the tell that distinguishes a real equation tag from a
    bare running page number, which never carries them."""
    return "(" in text and ")" in text


def content_x_range(blocks: list[dict]) -> tuple[float, float] | None:
    """Leftmost/rightmost block edge across `blocks`, i.e. the actual
    horizontal span of content -- the single source of truth for "content
    width" shared by detect_gutter (per-column split) and content_x_extent
    (single-column fallback), so the two stay defined identically rather than
    drifting apart. None if `blocks` is empty."""
    bboxes = [block_bbox(b) for b in blocks]
    if not bboxes:
        return None
    return min(bb.x0 for bb in bboxes), max(bb.x1 for bb in bboxes)


def content_x_extent(blocks: list[dict], fallback_width: float) -> float:
    """Span of actual text content on the page (rightmost block edge minus
    leftmost), not the raw page width. Pages with wide unused margins would
    otherwise make a real, complete column look like a fraction of the page
    far smaller than its fraction of the content region."""
    x_range = content_x_range(blocks)
    if x_range is None:
        return fallback_width
    return x_range[1] - x_range[0]
