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
    the normal reading flow, so it's excluded rather than misclassified."""
    return bbox.width < 25.0 and bbox.height > 100.0


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
