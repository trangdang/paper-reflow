"""Page-break planning. `plan_pagination` is the single source of truth for
where output pages break, shared by the final renderer so pagination is
identical to any plan derived here."""

from lib import config
from lib.elements import Element, Kind


def scaled_height(el: Element, target_width: float) -> float:
    bb = el.padded_bbox
    if bb.width <= 0:
        return 0.0
    return bb.height * (target_width / bb.width)


def plan_pagination(
    sequence: list[Element],
    target_width: float = config.TARGET_COLUMN_WIDTH_PT,
    page_height: float = config.OUTPUT_PAGE_HEIGHT_PT,
    margin: float = config.OUTPUT_MARGIN_PT,
    gap: float = config.INTER_ELEMENT_GAP_PT,
) -> list[list[tuple[Element, float, float]]]:
    """Greedily fill output pages, never splitting an element across a break.

    Returns a list of pages; each page is a list of (element, y_offset, height)
    tuples giving the element's vertical position within that output page.
    Consumed by render_final so the rendered page breaks match this plan.
    """
    usable_height = page_height - 2 * margin
    pages: list[list[tuple[Element, float, float]]] = []
    current: list[tuple[Element, float, float]] = []
    y_cursor = 0.0

    for el in sequence:
        h = scaled_height(el, target_width)
        needed = h if not current else h + gap
        if current and y_cursor + needed > usable_height:
            pages.append(current)
            current = []
            y_cursor = 0.0

        if el.kind == Kind.PAGE_BREAK and not current and pages:
            # Would otherwise land alone at the top of a new output page --
            # glue it to the bottom of the page it's closing instead, even
            # if that means slightly overflowing the usable height.
            prev_page = pages[-1]
            prev_y = max(yo + hh for _, yo, hh in prev_page)
            prev_page.append((el, prev_y + gap, h))
            continue

        y_offset = 0.0 if not current else y_cursor + gap
        current.append((el, y_offset, h))
        y_cursor = y_offset + h

    if current:
        pages.append(current)

    return pages
