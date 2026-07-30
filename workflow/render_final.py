"""Assembles the final vector-preserving PDF by reusing the same pagination
plan as render_draft, then replaying each element via show_pdf_page(clip=...)
so the output stays real vector content (selectable text, crisp figures)."""

import fitz

from lib import config
from lib.elements import Element, Kind
from workflow.render_draft import draw_page_break_marker, plan_pagination


def render_final(
    src_doc: fitz.Document,
    sequence: list[Element],
    output_path: str,
    target_width: float = config.TARGET_COLUMN_WIDTH_PT,
    page_height: float = config.OUTPUT_PAGE_HEIGHT_PT,
    margin: float = config.OUTPUT_MARGIN_PT,
) -> None:
    pages = plan_pagination(sequence, target_width, page_height, margin)
    page_width = target_width + 2 * margin

    out_doc = fitz.open()
    for page_items in pages:
        content_height = max(y_offset + h for _, y_offset, h in page_items)
        out_page = out_doc.new_page(width=page_width, height=content_height + 2 * margin)
        for el, y_offset, h in page_items:
            if el.kind == Kind.PAGE_BREAK:
                draw_page_break_marker(out_page, el, margin, y_offset, target_width, h)
                continue
            clip = fitz.Rect(*el.padded_bbox.as_tuple())
            rect = fitz.Rect(
                margin, margin + y_offset, margin + target_width, margin + y_offset + h
            )
            out_page.show_pdf_page(rect, src_doc, el.page_no, clip=clip)

    out_doc.save(output_path, garbage=4, deflate=True)
    out_doc.close()
