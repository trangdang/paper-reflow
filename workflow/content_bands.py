"""Header/footer running-stamp detection and document-wide content-band
computation: the (header_bottom, footer_top) y-range body content is
confined to on each page, and the text-block extraction that excludes
anything outside it."""

import statistics

import fitz

from lib import config
from lib.blocks import block_bbox, block_text, is_equation_tag, is_rotated_margin_stamp
from lib.elements import Bbox


def _is_small_stamp(bbox: Bbox) -> bool:
    return (
        bbox.height < config.HEADER_FOOTER_MAX_HEIGHT_PT
        and bbox.width < config.HEADER_FOOTER_MAX_WIDTH_PT
    )


def header_footer_stamp(bbox: Bbox, page_rect: fitz.Rect, text: str = "") -> str | None:
    """Classify bbox as a top ("header") or bottom ("footer") running stamp
    (page number / running head), or None if it isn't one. Same size/position
    test as the stripping in get_text_blocks, but reports which band so the
    document-wide content-band consensus can be built per side.

    `text` is the block's text: a parenthesized equation tag (e.g. '(184)')
    sitting at the foot of a column is small, low, and digit-bearing enough to
    trip the size/position test, but is real body content, not a running
    stamp, so it's rejected up front (is_equation_tag)."""
    band = config.HEADER_FOOTER_BAND_FRACTION * page_rect.height
    if not _is_small_stamp(bbox):
        return None
    if is_equation_tag(text):
        return None
    if bbox.y1 <= band:
        return "header"
    if bbox.y0 >= page_rect.height - band:
        return "footer"
    return None


def _is_header_footer(bbox: Bbox, page_rect: fitz.Rect, text: str = "") -> bool:
    return header_footer_stamp(bbox, page_rect, text) is not None


def norm_running(text: str) -> str:
    """Letters-only, lowercased form of a block's text, for running-head/footer
    repetition matching. The per-page page number and all punctuation drop out,
    so 'Glauz and Harwood 7' and 'Glauz and Harwood' both reduce to
    'glauzandharwood', and a copyright line's varying page-number suffix
    ('...IEEE.784' vs '...IEEE.') reduces to a stable 'ieee'."""
    return "".join(c for c in text.lower() if c.isalpha())


def _text_blocks_with_bbox(page: fitz.Page, text_dict: dict | None = None):
    d = text_dict if text_dict is not None else page.get_text("dict")
    for b in d["blocks"]:
        if b["type"] != 0:
            continue
        bbox = block_bbox(b)
        if bbox.width <= 0 or bbox.height <= 0:
            continue
        yield b, bbox


def detect_content_bands(pages, text_dicts: list[dict] | None = None) -> tuple[float, float]:
    """Document-wide vertical content band: the (header_bottom, footer_top)
    y-range that body content is confined to, i.e. below the running header
    strip and above the footer strip. Text blocks outside this band
    (get_text_blocks) and figure/graphic clusters bleeding into it
    (build_page_layout) are dropped or trimmed so running heads/footers stay
    out of the output.

    The strip is a fixed-height document constant, applied uniformly to every
    page; the first page (index 0) carries a different masthead and is
    excluded from the consensus (the band is still applied to it, catching a
    first-page running head that matches the rest). Two signals feed the
    band: small page-number stamps (header_footer_stamp), and wider running
    heads/footers detected by recurrence -- a block whose letters-only text
    shows up near the same edge on RUNNING_HEAD_MIN_PAGES or more pages.
    Recurrence, not mere position, is what tells a running head from a
    one-off first-page title or a section heading sitting high on the page.

    Header edge is 0.0 and footer edge is page height when nothing is
    detected.

    `text_dicts`, if given, is each page's already-parsed `get_text("dict")`
    result (same order as `pages`), reused here instead of re-parsing --
    callers that also need the parsed dict elsewhere (e.g. build_page_layout)
    should parse once and pass it through."""
    if not len(pages):
        height = 0.0
    else:
        # Sample heights across pages rather than trusting page 0 alone -- the
        # first page often has bespoke title-page spacing (or, rarely, a
        # differently sized page), so it's excluded from the consensus
        # whenever there are enough other pages to form one.
        sample = pages[1:] if len(pages) > 1 else pages
        height = statistics.mode(p.rect.height for p in sample)
    zone = config.HEADER_FOOTER_ZONE_FRACTION * height

    # Parsed once per page (not per pass) and reused below; also reused by
    # build_page_layout when the caller threads its own text_dicts through.
    dicts = text_dicts if text_dicts is not None else [p.get_text("dict") for p in pages]

    # First pass: which normalized texts recur near the top (headers) / bottom
    # (footers), counted by distinct page so a block repeated within one page
    # doesn't self-qualify.
    top_pages: dict[str, set] = {}
    bot_pages: dict[str, set] = {}
    for i, page in enumerate(pages):
        for b, bbox in _text_blocks_with_bbox(page, dicts[i]):
            norm = norm_running(block_text(b))
            if len(norm) < 2:
                continue
            if bbox.y1 <= zone:
                top_pages.setdefault(norm, set()).add(i)
            elif bbox.y0 >= height - zone:
                bot_pages.setdefault(norm, set()).add(i)
    top_recurring = {n for n, ps in top_pages.items() if len(ps) >= config.RUNNING_HEAD_MIN_PAGES}
    bot_recurring = {n for n, ps in bot_pages.items() if len(ps) >= config.RUNNING_HEAD_MIN_PAGES}

    # Second pass: measure the band extent from header/footer blocks. Three
    # signals contribute:
    #   * a small stamp within the tight 5% band (header_footer_stamp) -- the
    #     baseline page-number detector, all a document with no running head
    #     (just page numbers, e.g. micro_lie) ever needs;
    #   * a recurring running head/footer (letters-only text seen on several
    #     pages in the zone);
    #   * a page-number stamp (small, digit-only block) sitting in the wider
    #     zone but below the 5% band -- but ONLY when a running head/footer
    #     already defines that row. A page number often shares the running
    #     head's row a little below the 5% band (a "9" at y~48), and the
    #     band must reach it or the "9" survives as a body
    #     element whose clip re-renders the running head. Gating this on an
    #     established running head is what stops a lone small digit-ish math
    #     fragment near a footer-less page edge from inventing a spurious band.
    # First page excluded from the consensus.
    have_header = bool(top_recurring)
    have_footer = bool(bot_recurring)
    tops: list[float] = []
    bots: list[float] = []
    for i, page in enumerate(pages):
        if i == 0:
            continue
        for b, bbox in _text_blocks_with_bbox(page, dicts[i]):
            text = block_text(b)
            norm = norm_running(text)
            side = header_footer_stamp(bbox, page.rect, text)
            digit_stamp = _is_small_stamp(bbox) and norm == "" and any(c.isdigit() for c in text)
            is_header = side == "header" or (
                bbox.y1 <= zone and (norm in top_recurring or (have_header and digit_stamp))
            )
            in_footer_zone = bbox.y0 >= height - zone
            is_footer = side == "footer" or (
                in_footer_zone and (norm in bot_recurring or (have_footer and digit_stamp))
            )
            if is_header:
                tops.append(bbox.y1)
            elif is_footer:
                bots.append(bbox.y0)
    top = max(tops) if tops else 0.0
    bot = min(bots) if bots else height
    return top, bot


def get_text_blocks(
    page: fitz.Page,
    content_band: tuple[float, float] | None = None,
    text_dict: dict | None = None,
) -> tuple[list[dict], list[str]]:
    """Text blocks (type==0) from get_text('dict'), with header/footer blocks
    excluded. Also returns the text of blocks excluded as header/footer (a
    running page-number stamp, a running head/footer, etc.) or rotated margin
    stamps (e.g. a rotated arXiv identifier), since that text is intentionally
    dropped from the reflowed output and callers need it to record the
    exclusion for word-fidelity checking.

    `content_band` is the (header_bottom, footer_top) y-range of body content
    for this page (see detect_content_bands). Any block lying entirely above
    header_bottom or below footer_top is a running head/footer and is dropped
    -- this is what removes the wide running heads (journal name, author list)
    and footer boilerplate (copyright line) that the small-stamp test misses.

    `text_dict` is an already-parsed `page.get_text("dict")` result, reused
    instead of re-parsing when the caller has one on hand (e.g. reflow.py
    parses each page once up front and threads it through here and through
    detect_content_bands)."""
    header_bottom, footer_top = content_band or (0.0, page.rect.height)
    d = text_dict if text_dict is not None else page.get_text("dict")
    out = []
    stamp_texts = []
    for b in d["blocks"]:
        if b["type"] != 0:
            continue
        bbox = block_bbox(b)
        if bbox.width <= 0 or bbox.height <= 0:
            continue
        if _is_header_footer(bbox, page.rect, block_text(b)):
            stamp_texts.append(block_text(b))
            continue
        if bbox.y1 <= header_bottom or bbox.y0 >= footer_top:
            stamp_texts.append(block_text(b))
            continue
        if is_rotated_margin_stamp(bbox):
            stamp_texts.append(block_text(b))
            continue
        out.append(b)
    return out, stamp_texts
