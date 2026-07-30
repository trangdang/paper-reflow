"""Builds the raster layout-draft PDF (column-width crops stacked into
fixed-height output pages) and the bbox-overlay debug PDF."""

import fitz

from lib import config
from lib.elements import Element, PageLayout


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
    Shared by render_draft and render_final so pagination is identical.
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

        y_offset = 0.0 if not current else y_cursor + gap
        current.append((el, y_offset, h))
        y_cursor = y_offset + h

    if current:
        pages.append(current)

    return pages


def render_draft(
    src_doc: fitz.Document,
    sequence: list[Element],
    output_path: str,
    target_width: float = config.TARGET_COLUMN_WIDTH_PT,
    page_height: float = config.OUTPUT_PAGE_HEIGHT_PT,
    margin: float = config.OUTPUT_MARGIN_PT,
) -> list[list[tuple[Element, float, float]]]:
    pages = plan_pagination(sequence, target_width, page_height, margin)
    page_width = target_width + 2 * margin

    out_doc = fitz.open()
    for page_items in pages:
        content_height = max(y_offset + h for _, y_offset, h in page_items)
        out_page = out_doc.new_page(width=page_width, height=content_height + 2 * margin)
        for el, y_offset, h in page_items:
            src_page = src_doc[el.page_no]
            clip = fitz.Rect(*el.padded_bbox.as_tuple())
            zoom = config.DRAFT_DPI / 72.0
            pix = src_page.get_pixmap(clip=clip, matrix=fitz.Matrix(zoom, zoom))
            rect = fitz.Rect(margin, margin + y_offset, margin + target_width, margin + y_offset + h)
            out_page.insert_image(rect, pixmap=pix)

    out_doc.save(output_path)
    out_doc.close()
    return pages


_KIND_COLORS = {
    "figure": (1, 0, 0),
    "table": (0, 0, 1),
    "graphic": (1, 0.5, 0),
    "heading": (0, 0.6, 0),
    "paragraph": (0.5, 0.5, 0.5),
    "equation": (0.6, 0, 0.6),
    "other": (0, 0, 0),
}


def render_overlay(src_doc: fitz.Document, page_layouts: list[PageLayout], output_path: str) -> None:
    """Debug PDF: original pages with tight (dashed color) + padded (solid
    black) element bboxes and kind/column labels drawn on."""
    out_doc = fitz.open()
    for layout in sorted(page_layouts, key=lambda p: p.page_no):
        src_page = src_doc[layout.page_no]
        out_page = out_doc.new_page(width=src_page.rect.width, height=src_page.rect.height)
        out_page.show_pdf_page(out_page.rect, src_doc, layout.page_no)

        if layout.gutter_x:
            gx0, gx1 = layout.gutter_x
            out_page.draw_rect(
                fitz.Rect(gx0, 0, gx1, src_page.rect.height), color=(1, 1, 0), fill=(1, 1, 0), fill_opacity=0.2
            )

        for el in layout.elements:
            color = _KIND_COLORS.get(el.kind.value, (0, 0, 0))
            out_page.draw_rect(fitz.Rect(*el.bbox.as_tuple()), color=color, width=1)
            if el.padded_bbox is not None:
                out_page.draw_rect(
                    fitz.Rect(*el.padded_bbox.as_tuple()), color=color, width=0.5, dashes="[2] 0"
                )
            label = f"{el.kind.value}/{el.column.value}"
            out_page.insert_text((el.bbox.x0, max(8, el.bbox.y0 - 2)), label, fontsize=6, color=color)

    out_doc.save(output_path)
    out_doc.close()
