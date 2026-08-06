"""Page-break planning. `plan_pagination` is the single source of truth for
where output pages break, shared by the final renderer so pagination is
identical to any plan derived here."""

from typing import NamedTuple

from lib import config
from lib.elements import Column, Element, Kind


class PlacedElement(NamedTuple):
    """An element positioned and scaled onto an output page by plan_pagination."""

    element: Element
    y_offset: float
    height: float
    width: float
    x_offset: float


def common_single_column_width(sequence: list[Element]) -> float:
    """Max padded-bbox width across elements confined to a single column
    (LEFT/RIGHT/SINGLE), used as a shared width reference so a narrower block
    (e.g. a tightly-boxed display equation) scales at the same factor as the
    surrounding column text instead of blowing up to fill target_width on its
    own. SPANNING elements and the synthetic PAGE_BREAK marker are excluded --
    they always scale to their own full width regardless of this value."""
    widths = [
        el.padded_bbox.width
        for el in sequence
        if el.column != Column.SPANNING and el.kind != Kind.PAGE_BREAK and el.padded_bbox.width > 0
    ]
    return max(widths) if widths else 0.0


def scaled_dims(el: Element, target_width: float, common_width: float) -> tuple[float, float]:
    """Width and height el occupies once scaled into the output page.

    SPANNING elements and the PAGE_BREAK marker scale by their own bbox width
    so they fill target_width exactly. Column-confined elements (LEFT/RIGHT/
    SINGLE) instead scale by the shared target_width/common_width factor, so a
    narrower block keeps the same font size as its column neighbors -- its
    scaled width then comes out below target_width rather than stretched to
    fill it.
    """
    bb = el.padded_bbox
    if bb.width <= 0:
        return 0.0, 0.0
    uses_own_width = el.column == Column.SPANNING or el.kind == Kind.PAGE_BREAK
    ref_width = bb.width if uses_own_width or common_width <= 0 else common_width
    scale = target_width / ref_width
    return bb.width * scale, bb.height * scale


def plan_pagination(
    sequence: list[Element],
    target_width: float = config.TARGET_COLUMN_WIDTH_PT,
    page_height: float = config.OUTPUT_PAGE_HEIGHT_PT,
    margin: float = config.OUTPUT_MARGIN_PT,
    gap: float = config.INTER_ELEMENT_GAP_PT,
) -> list[list[PlacedElement]]:
    """Greedily fill output pages, never splitting an element across a break.

    Returns a list of pages; each page is a list of PlacedElements giving the
    element's position and scaled size within that output page. Consumed by
    render_final so the rendered page breaks and element placement match this
    plan.
    """
    common_width = common_single_column_width(sequence)
    usable_height = page_height - 2 * margin
    pages: list[list[PlacedElement]] = []
    current: list[PlacedElement] = []
    y_cursor = 0.0

    for el in sequence:
        w, h = scaled_dims(el, target_width, common_width)
        # Elements narrower than target_width (LEFT/RIGHT/SINGLE elements
        # scaled by common_width rather than their own width) are centered
        # horizontally; SPANNING/PAGE_BREAK elements already come out at
        # width == target_width, so this is a no-op (x_offset == 0) for them.
        x_offset = (target_width - w) / 2
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
            prev_y = max(placed.y_offset + placed.height for placed in prev_page)
            prev_page.append(PlacedElement(el, prev_y + gap, h, w, x_offset))
            continue

        y_offset = 0.0 if not current else y_cursor + gap
        current.append(PlacedElement(el, y_offset, h, w, x_offset))
        y_cursor = y_offset + h

    if current:
        pages.append(current)

    return pages
