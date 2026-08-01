"""Per-page layout detection orchestrator: runs gutter/column detection,
figure/table/caption clustering, and the element-merging pipeline to build
each page's PageLayout."""

import fitz

from lib import config
from lib.blocks import block_bbox, block_text, content_x_extent
from lib.elements import Bbox, Column, Element, Kind, PageLayout
from workflow.content_bands import get_text_blocks
from workflow.element_merging import (
    _body_font_size,
    _complete_undersized_elements,
    _is_heading,
    _median_line_height,
    _merge_adjacent_paragraphs,
    _merge_overlapping_same_column_elements,
    _merge_overlapping_same_kind_elements,
    _reclassify_ambiguous_width_band,
)
from workflow.figures import (
    _build_figure_graphic_elements,
    _merge_orphan_figure_captions,
    _merge_table_rule_regions,
    _trim_figures_to_content_band,
)
from workflow.gutter import classify_or_single, detect_gutter, pin_gutter_width


def build_page_layout(
    page: fitz.Page,
    page_no: int,
    gutter_width_override: float | None = None,
    content_band: tuple[float, float] | None = None,
    text_dict: dict | None = None,
) -> PageLayout:
    text_blocks, excluded_texts = get_text_blocks(page, content_band, text_dict)
    is_two_col, left_col_x, right_col_x, gutter_x = detect_gutter(page, text_blocks)
    if is_two_col and gutter_width_override is not None:
        left_col_x, right_col_x, gutter_x = pin_gutter_width(
            left_col_x, right_col_x, gutter_x, gutter_width_override
        )
    body_font_size = _body_font_size(text_blocks)
    median_line_height = _median_line_height(text_blocks)

    used_text_idx: set = set()
    elements: list[Element] = _build_figure_graphic_elements(
        page, text_blocks, used_text_idx, page_no, is_two_col, left_col_x, right_col_x
    )

    # Remaining text blocks -> paragraphs / headings.
    for idx, b in enumerate(text_blocks):
        if idx in used_text_idx:
            continue
        bbox = block_bbox(b)
        text = block_text(b)
        column = classify_or_single(bbox, is_two_col, left_col_x, right_col_x)
        # Headings (section titles) span the full column width in this
        # two-column layout; a within-column block with a large font is
        # more likely a display equation or emphasized text, not a heading.
        is_spanning = column in (Column.SPANNING, Column.SINGLE)
        kind = Kind.HEADING if is_spanning and _is_heading(b, body_font_size) else Kind.PARAGRAPH
        elements.append(Element(kind=kind, page_no=page_no, column=column, bbox=bbox, text=text))

    elements = _merge_table_rule_regions(elements, page, page_no)
    for el in elements:
        el.column = classify_or_single(el.bbox, is_two_col, left_col_x, right_col_x)
    elements = _merge_adjacent_paragraphs(elements, median_line_height)
    elements = _merge_overlapping_same_kind_elements(elements)
    elements = _merge_overlapping_same_column_elements(elements)
    content_width = content_x_extent(text_blocks, page.rect.width)
    elements = _complete_undersized_elements(elements, content_width, median_line_height)
    elements = _merge_overlapping_same_kind_elements(elements)
    elements = _merge_overlapping_same_column_elements(elements)
    _reclassify_ambiguous_width_band(elements, is_two_col, left_col_x, right_col_x)
    elements = _merge_orphan_figure_captions(elements)

    if content_band is not None:
        _trim_figures_to_content_band(elements, content_band)

    elements.sort(key=lambda e: e.y0)

    return PageLayout(
        page_no=page_no,
        left_col_x=left_col_x,
        right_col_x=right_col_x,
        gutter_x=gutter_x,
        is_two_column=is_two_col,
        elements=elements,
        content_band=content_band,
        excluded_texts=excluded_texts,
    )
