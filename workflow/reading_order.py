"""Builds the flat, document-wide reading-order sequence from per-page
classified elements: left-column top-to-bottom, then right-column
top-to-bottom, with spanning elements splitting both columns at their
own vertical position."""

from lib import config
from lib.elements import Bbox, Column, Element, Kind, PageLayout


def order_page(elements: list[Element]) -> list[Element]:
    """Reading order for a single page's elements.

    Rule: walk spanning elements top to bottom; before emitting each one,
    flush all left-column then right-column elements that are fully above
    it. After the last spanning element, flush any remaining left then
    right elements. Single-column pages have no LEFT/RIGHT split, so this
    degenerates to a plain top-to-bottom order (SINGLE elements behave
    like a lone column with no spanning interleaving needed, since
    there's nothing to interleave against).
    """
    left = sorted((e for e in elements if e.column == Column.LEFT), key=lambda e: e.y0)
    right = sorted((e for e in elements if e.column == Column.RIGHT), key=lambda e: e.y0)
    spanning = sorted((e for e in elements if e.column == Column.SPANNING), key=lambda e: e.y0)
    single = sorted((e for e in elements if e.column == Column.SINGLE), key=lambda e: e.y0)

    if single and not left and not right and not spanning:
        return single
    if single:
        # Mixed single-column content on an otherwise two-column page:
        # treat SINGLE elements like SPANNING for ordering purposes.
        spanning = sorted(spanning + single, key=lambda e: e.y0)

    out: list[Element] = []
    li = ri = 0

    def flush_above(y: float):
        nonlocal li, ri
        while li < len(left) and left[li].y1 <= y:
            out.append(left[li])
            li += 1
        while ri < len(right) and right[ri].y1 <= y:
            out.append(right[ri])
            ri += 1

    for s in spanning:
        flush_above(s.y0)
        out.append(s)

    while li < len(left):
        out.append(left[li])
        li += 1
    while ri < len(right):
        out.append(right[ri])
        ri += 1

    return out


def build_reading_order(page_layouts: list[PageLayout]) -> list[Element]:
    """Concatenate each page's reading order in page order."""
    sequence: list[Element] = []
    for layout in sorted(page_layouts, key=lambda p: p.page_no):
        sequence.extend(order_page(layout.elements))
    return sequence


def _page_break_marker(page_no: int, column: Column) -> Element:
    marker_bbox = Bbox(0, 0, config.TARGET_COLUMN_WIDTH_PT, config.PAGE_BREAK_HEIGHT_PT)
    return Element(
        kind=Kind.PAGE_BREAK,
        page_no=page_no,
        column=column,
        bbox=marker_bbox,
        padded_bbox=marker_bbox,
        text=f"Page {page_no + 1}",
    )


def insert_page_breaks(sequence: list[Element]) -> list[Element]:
    """Insert a synthetic PAGE_BREAK marker after the last element of each
    source page (including the final one), so the reflowed output can be
    traced back to the original PDF page it came from. The marker's bbox is
    sized to occupy exactly PAGE_BREAK_HEIGHT_PT once scaled to the output
    column width, regardless of target_width, since scaled_height() scales
    by (target_width / bbox width) and the marker bbox width is fixed to
    config.TARGET_COLUMN_WIDTH_PT.
    """
    out: list[Element] = []
    last_el: Element | None = None
    for el in sequence:
        if last_el is not None and el.page_no != last_el.page_no:
            out.append(_page_break_marker(last_el.page_no, last_el.column))
        out.append(el)
        last_el = el
    if last_el is not None:
        out.append(_page_break_marker(last_el.page_no, last_el.column))
    return out
