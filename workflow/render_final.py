"""Assembles the final vector-preserving PDF from the shared pagination plan
(see workflow.pagination), replaying each element via show_pdf_page(clip=...)
so the output stays real vector content (selectable text, crisp figures)."""

import fitz

from lib import config
from lib.elements import Element, Kind
from workflow.pagination import plan_pagination


def draw_page_break_marker(
    page: fitz.Page, el: Element, margin: float, y_offset: float, width: float, height: float
) -> None:
    """Render a subtle 'page N' label + light gray rule marking where the
    original source PDF's page boundary fell in the reading order."""
    text_y = margin + y_offset + height * 0.65
    line_y = margin + y_offset + height * 0.85
    page.insert_text(
        (margin, text_y),
        el.text,
        fontsize=config.PAGE_BREAK_FONT_SIZE,
        color=config.PAGE_BREAK_TEXT_COLOR,
    )
    page.draw_line(
        (margin, line_y),
        (margin + width, line_y),
        color=config.PAGE_BREAK_LINE_COLOR,
        width=config.PAGE_BREAK_LINE_WIDTH,
    )


def render_final(
    src_doc: fitz.Document,
    sequence: list[Element],
    target_width: float = config.TARGET_COLUMN_WIDTH_PT,
    page_height: float = config.OUTPUT_PAGE_HEIGHT_PT,
    margin: float = config.OUTPUT_MARGIN_PT,
) -> fitz.Document:
    """Build the final reflowed PDF and return the open Document. Callers own
    persistence (CLI saves to a path; the browser adapter serializes to bytes)."""
    pages = plan_pagination(sequence, target_width, page_height, margin)
    page_width = target_width + 2 * margin

    out_doc = fitz.open()
    for page_items in pages:
        content_height = max(placed.y_offset + placed.height for placed in page_items)
        out_page = out_doc.new_page(width=page_width, height=content_height + 2 * margin)
        for el, y_offset, h, w, x_offset in page_items:
            if el.kind == Kind.PAGE_BREAK:
                draw_page_break_marker(out_page, el, margin, y_offset, target_width, h)
                continue
            clip = fitz.Rect(*el.padded_bbox.as_tuple())
            x0 = margin + x_offset
            rect = fitz.Rect(x0, margin + y_offset, x0 + w, margin + y_offset + h)
            out_page.show_pdf_page(rect, src_doc, el.page_no, clip=clip)

    return out_doc
