"""Debug PDF renderer: draws the per-page detection output (element bboxes,
kind/column labels, gutter region) onto the original pages. This renders the
layout-detection result, not the paginated reflow -- see render_final for the
actual output PDF."""

import fitz

from lib.elements import PageLayout

_KIND_COLORS = {
    "figure": (1, 0, 0),
    "table": (0, 0, 1),
    "graphic": (1, 0.5, 0),
    "heading": (0, 0.6, 0),
    "paragraph": (0.5, 0.5, 0.5),
    "equation": (0.6, 0, 0.6),
    "other": (0, 0, 0),
}


def render_overlay(
    src_doc: fitz.Document, page_layouts: list[PageLayout], output_path: str
) -> None:
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
                fitz.Rect(gx0, 0, gx1, src_page.rect.height),
                color=(1, 1, 0),
                fill=(1, 1, 0),
                fill_opacity=0.2,
            )

        for el in layout.elements:
            color = _KIND_COLORS.get(el.kind.value, (0, 0, 0))
            out_page.draw_rect(fitz.Rect(*el.bbox.as_tuple()), color=color, width=1)
            if el.padded_bbox is not None:
                out_page.draw_rect(
                    fitz.Rect(*el.padded_bbox.as_tuple()), color=color, width=0.5, dashes="[2] 0"
                )
            label = f"{el.kind.value}/{el.column.value}"
            out_page.insert_text(
                (el.bbox.x0, max(8, el.bbox.y0 - 2)), label, fontsize=6, color=color
            )

    out_doc.save(output_path)
    out_doc.close()
