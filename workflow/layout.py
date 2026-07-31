"""Per-page layout detection: gutter/column split, block classification,
element grouping, and bbox padding/whitespace-snapping."""

import statistics

import fitz

from lib import config
from lib.blocks import _block_bbox, _block_text, _content_x_extent
from lib.elements import Bbox, Column, Element, Kind, PageLayout
from workflow.content_bands import get_text_blocks
from workflow.figures import (
    _build_figure_graphic_elements,
    _merge_orphan_figure_captions,
    _merge_table_rule_regions,
    _trim_figures_to_content_band,
)
from workflow.gutter import classify_block, detect_gutter, pin_gutter_width


def _median_line_height(blocks: list[dict]) -> float:
    heights = []
    for b in blocks:
        for line in b["lines"]:
            spans = line["spans"]
            if not spans:
                continue
            y0 = min(s["bbox"][1] for s in spans)
            y1 = max(s["bbox"][3] for s in spans)
            if y1 > y0:
                heights.append(y1 - y0)
    return statistics.median(heights) if heights else 10.0


def _merge_adjacent_paragraphs(elements: list[Element], median_line_height: float) -> list[Element]:
    """Merge adjacent blocks in the same column if the gap between them is smaller
    than the median line height, unless a heading (font-size jump) sits between them.
    Headings are identified upstream and kept as their own Kind.HEADING elements,
    so we simply never merge across an element whose kind is HEADING."""
    if not elements:
        return elements

    by_column: dict[Column, list[Element]] = {}
    for el in elements:
        by_column.setdefault(el.column, []).append(el)

    merged: list[Element] = []
    for col, els in by_column.items():
        els.sort(key=lambda e: e.y0)
        current = None
        for el in els:
            if el.kind != Kind.PARAGRAPH:
                if current is not None:
                    merged.append(current)
                    current = None
                merged.append(el)
                continue
            if current is None:
                current = el
                continue
            gap = el.y0 - current.y1
            if 0 <= gap <= median_line_height:
                current.bbox = current.bbox.union(el.bbox)
                current.text = current.text + "\n" + el.text
                current.source_refs.extend(el.source_refs)
            else:
                merged.append(current)
                current = el
        if current is not None:
            merged.append(current)
    return merged


def _is_heading(block: dict, body_font_size: float) -> bool:
    sizes = [s["size"] for line in block["lines"] for s in line["spans"]]
    if not sizes:
        return False
    return max(sizes) > body_font_size * 1.15


def _body_font_size(blocks: list[dict]) -> float:
    sizes = []
    for b in blocks:
        for line in b["lines"]:
            for s in line["spans"]:
                sizes.append(s["size"])
    return statistics.median(sizes) if sizes else 10.0


_KIND_MERGE_PRIORITY = [
    Kind.FIGURE,
    Kind.TABLE,
    Kind.EQUATION,
    Kind.HEADING,
    Kind.GRAPHIC,
    Kind.PARAGRAPH,
    Kind.OTHER,
]


def _merged_kind(a: Kind, b: Kind) -> Kind:
    for k in _KIND_MERGE_PRIORITY:
        if a == k or b == k:
            return k
    return Kind.OTHER


def _merge_overlapping_same_column_elements(elements: list[Element]) -> list[Element]:
    """Two elements sharing the same column classification (both LEFT, both
    RIGHT, or both SPANNING) whose tight bboxes overlap are, by construction,
    the same piece of content seen as multiple fragments (e.g. a table's
    dense cell text left un-absorbed by an overlapping caption/label
    element) -- render them as separate clip regions and the overlapping
    area gets duplicated into the output twice. Merge any such pair,
    repeated to a fixed point. This is independent of element width: unlike
    _complete_undersized_elements, it fires purely on overlap, not size.

    Uses BBOX_OVERLAP_TOLERANCE_PT as the intersection tolerance rather than
    a bare intersects() check: adjacent same-column paragraphs routinely
    share a sub-pixel sliver of overlap from PyMuPDF's ascender/descender
    padding even when correctly split, and treating that as "overlapping"
    would cascade-merge an entire column into one oversized element that
    then overflows a single output page during pagination (each element
    must fit on one output page; pagination never splits one)."""
    tol = config.BBOX_OVERLAP_TOLERANCE_PT
    elements = list(elements)
    changed = True
    while changed:
        changed = False
        n = len(elements)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = elements[i], elements[j]
                if a.column == b.column and a.bbox.intersects(b.bbox, tolerance=tol):
                    merged = Element(
                        kind=_merged_kind(a.kind, b.kind),
                        page_no=a.page_no,
                        column=a.column,
                        bbox=a.bbox.union(b.bbox),
                        text="\n".join(t for t in (a.text, b.text) if t),
                        source_refs=a.source_refs + b.source_refs,
                    )
                    elements = [e for k, e in enumerate(elements) if k not in (i, j)] + [merged]
                    changed = True
                    break
            if changed:
                break
    return elements


def _merge_overlapping_same_kind_elements(elements: list[Element]) -> list[Element]:
    """Two elements that share both column and kind (e.g. two LEFT-column
    paragraphs) whose tight bboxes overlap at all -- even a sub-pixel sliver
    below BBOX_OVERLAP_TOLERANCE_PT -- are the same logical unit split by
    extraction noise (line-height rounding, ascender/descender box padding).
    Unlike _merge_overlapping_same_column_elements, this doesn't need the
    tolerance to guard against over-merging unrelated content: restricting to
    matching kind already rules out folding a heading into a paragraph or a
    table into a caption, so any genuine overlap is safe to merge. Iterated
    to a fixed point. This catches exactly the borderline case the
    column-only pass deliberately leaves alone (to avoid cascade-merging an
    entire column) -- one padding can't fully snap away, which otherwise
    surfaces as a padded-bbox-overlap warning."""
    elements = list(elements)
    changed = True
    while changed:
        changed = False
        n = len(elements)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = elements[i], elements[j]
                if a.column == b.column and a.kind == b.kind and a.bbox.intersects(b.bbox):
                    merged = Element(
                        kind=a.kind,
                        page_no=a.page_no,
                        column=a.column,
                        bbox=a.bbox.union(b.bbox),
                        text="\n".join(t for t in (a.text, b.text) if t),
                        source_refs=a.source_refs + b.source_refs,
                    )
                    elements = [e for k, e in enumerate(elements) if k not in (i, j)] + [merged]
                    changed = True
                    break
            if changed:
                break
    return elements


def _complete_undersized_elements(
    elements: list[Element], content_width: float, median_line_height: float | None = None
) -> list[Element]:
    """Real technical-paper elements are essentially never an arbitrary
    fraction of the page's content width: they're either about one column
    wide or they span both columns. An element narrower than
    SINGLE_COLUMN_MIN_WIDTH_FRACTION is treated as an incomplete fragment
    (this is what happens to dense, borderless math-notation tables that the
    earlier proximity clustering didn't fully absorb) and is repeatedly
    merged with its nearest neighbor — any column/kind — until it crosses the
    single-column-width floor or no neighbor remains within the search
    radius, in which case it's left standalone (a truly isolated small
    icon/symbol shouldn't be force-inflated).

    content_width must be the actual text-content span (rightmost block edge
    minus leftmost), not the raw page width: a page with wide unused margins
    can have a real, complete column sit just under 40% of the *page* width
    while still being ~48% of the *content* width, which would otherwise
    misclassify every legitimate paragraph as an undersized fragment and
    cascade-merge the whole page into one spanning element.

    median_line_height tightens the search radius specifically when the
    candidate neighbor is already column-width-complete: a genuine trailing
    line-group sits within about one line height of its own paragraph, but a
    stray narrow fragment (e.g. a borderless table's label column, whose true
    partner columns are sideways rather than above/below it) can otherwise be
    the nearest thing by raw Chebyshev gap to an unrelated paragraph several
    lines away and get pulled into it. Incomplete-neighbor merges (the actual
    fragment-assembly case) are unaffected and keep the full search radius."""
    min_width = config.SINGLE_COLUMN_MIN_WIDTH_FRACTION * content_width
    max_gap = config.ELEMENT_MERGE_SEARCH_GAP_PT
    complete_neighbor_gap = (
        min(max_gap, median_line_height * config.UNDERSIZED_COMPLETE_NEIGHBOR_GAP_LINES)
        if median_line_height
        else max_gap
    )
    elements = list(elements)

    changed = True
    while changed:
        changed = False
        for i, el in enumerate(elements):
            if el.bbox.width >= min_width:
                continue
            best_j, best_dist = None, None
            for j, other in enumerate(elements):
                if j == i:
                    continue
                # A real column boundary is only worth crossing when the
                # neighbor is itself an incomplete fragment -- two narrow
                # halves split by the gutter are plausibly one logical
                # element. A neighbor that's already column-width-complete on
                # its own (e.g. an entire column of legitimately narrower
                # boxed/indented content) is real, unrelated content, not a
                # continuation fragment. This applies whether the undersized
                # side is itself LEFT/RIGHT or a leftover SPANNING fragment
                # (e.g. an unmerged row of a borderless table) -- an already-
                # complete LEFT/RIGHT paragraph below a table is exactly the
                # unrelated content this guard exists to protect, and without
                # this check a table's growing SPANNING blob can keep
                # cascading into it merge after merge.
                if (
                    el.column != other.column
                    and other.column in (Column.LEFT, Column.RIGHT)
                    and other.bbox.width >= min_width
                ):
                    continue
                dx = max(el.bbox.x0 - other.bbox.x1, other.bbox.x0 - el.bbox.x1, 0.0)
                dy = max(el.bbox.y0 - other.bbox.y1, other.bbox.y0 - el.bbox.y1, 0.0)
                dist = max(dx, dy)
                # The tighter, line-height-scaled radius only governs
                # vertical separation (a trailing line stacking under/over
                # its paragraph). A horizontal-only gap into a complete
                # neighbor -- e.g. a borderless table's rightmost column
                # sitting beside, not below, the rest of the table -- is a
                # different geometry and keeps the full search radius.
                gap_limit = (
                    complete_neighbor_gap if other.bbox.width >= min_width and dy >= dx else max_gap
                )
                if dist <= gap_limit and (best_dist is None or dist < best_dist):
                    best_j, best_dist = j, dist
            if best_j is None:
                continue  # genuinely isolated -- leave as a small standalone element
            other = elements[best_j]
            # Only force SPANNING when the merge actually crosses a column
            # boundary. Two fragments that both happen to sit in the same
            # column (the common case: a short trailing line-group merging
            # with its own paragraph) are still a single-column element and
            # must keep that label -- forcing SPANNING here mislabels them,
            # which then lets the overlap-merge pass cascade them into
            # unrelated content in the other column.
            merged_column = el.column if el.column == other.column else Column.SPANNING
            merged = Element(
                kind=_merged_kind(el.kind, other.kind),
                page_no=el.page_no,
                column=merged_column,
                bbox=el.bbox.union(other.bbox),
                text="\n".join(t for t in (el.text, other.text) if t),
                source_refs=el.source_refs + other.source_refs,
            )
            elements = [e for k, e in enumerate(elements) if k not in (i, best_j)] + [merged]
            changed = True
            break  # indices shifted -- restart the scan
    return elements


def _reclassify_ambiguous_width_band(
    elements: list[Element], is_two_col: bool, left_col_x, right_col_x
) -> None:
    """An element noticeably wider than this page's own single column but
    still well short of the full two-column span can't cleanly fit in one
    column and isn't confidently spanning either -- force it to SPANNING
    rather than leaving it in whatever ambiguous per-block classification
    it fell into. Thresholds are relative to the page's measured column
    width, not a fixed fraction of page width: page-width fractions are
    unreliable because real single-column content routinely sits right at
    ~40-48% of page width for normal margins/gutters, which a fixed
    0.40-0.60 band would wrongly treat as ambiguous."""
    if not is_two_col:
        return
    col_width = left_col_x[1] - left_col_x[0]
    content_span = right_col_x[1] - left_col_x[0]
    lo = col_width + config.AMBIGUOUS_WIDTH_MARGIN_PT
    hi = config.AMBIGUOUS_SPANNING_FRACTION * content_span
    if hi <= lo:
        return
    for el in elements:
        if lo <= el.bbox.width < hi:
            el.column = Column.SPANNING


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
        bbox = _block_bbox(b)
        text = _block_text(b)
        if is_two_col:
            column = classify_block(bbox, left_col_x, right_col_x)
        else:
            column = Column.SINGLE
        # Headings (section titles) span the full column width in this
        # two-column layout; a within-column block with a large font is
        # more likely a display equation or emphasized text, not a heading.
        is_spanning = column in (Column.SPANNING, Column.SINGLE)
        kind = Kind.HEADING if is_spanning and _is_heading(b, body_font_size) else Kind.PARAGRAPH
        elements.append(Element(kind=kind, page_no=page_no, column=column, bbox=bbox, text=text))

    elements = _merge_table_rule_regions(
        elements, page, page_no, is_two_col, left_col_x, right_col_x
    )
    elements = _merge_adjacent_paragraphs(elements, median_line_height)
    elements = _merge_overlapping_same_kind_elements(elements)
    elements = _merge_overlapping_same_column_elements(elements)
    content_width = _content_x_extent(text_blocks, page.rect.width)
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


# --- Bbox padding / whitespace-snapping (Milestone 4) ---


def pad_and_snap_bboxes(layout: PageLayout, page_rect: fitz.Rect) -> list[str]:
    """Compute padded/snapped clip bboxes for every element on a page.

    Mutates element.padded_bbox in place. Returns a list of warning strings
    for any residual overlaps beyond tolerance.
    """
    pad = config.VERTICAL_PAD_PT
    step = config.SNAP_STEP_PT
    # Stretch LEFT/RIGHT column clips out to the page's actual detected
    # content margins, not the raw page edges (0 / page_rect.width). The
    # physical page edge is typically well outside the printed text -- and
    # can contain page-margin content (e.g. a rotated arXiv identifier
    # stamp) that isn't part of any element but would otherwise get swept
    # into a column's clip region and rendered anyway. Using the true
    # per-page margins also keeps LEFT- and RIGHT-column elements visually
    # flush with each other instead of each carrying its own leftover
    # blank strip toward whichever page edge it happened to stretch to.
    margin_x0 = layout.left_col_x[0] if layout.left_col_x else 0.0
    margin_x1 = layout.right_col_x[1] if layout.right_col_x else page_rect.width

    # Vertical padding must not push a figure/graphic clip back into a reserved
    # header/footer band (which the tight-bbox trim above already pulled it out
    # of) -- clamp the padded y-range to the band, treating its edges like the
    # page margins. Same FIGURE/GRAPHIC-only scope and same crossing-edge-only
    # rule as that trim, so text elements keep their full padded extent.
    band_top, band_bot = layout.content_band or (0.0, page_rect.height)

    elements = layout.elements
    padded = []
    for el in elements:
        bb = el.bbox
        clamp = el.kind in (Kind.FIGURE, Kind.GRAPHIC)
        y0 = bb.y0 - pad
        y1 = bb.y1 + pad
        if clamp and bb.y1 > band_top:
            y0 = max(y0, band_top)
        if clamp and bb.y0 < band_bot:
            y1 = min(y1, band_bot)

        if el.column == Column.LEFT:
            x0 = margin_x0
            x1 = layout.gutter_x[1] if layout.gutter_x else margin_x1
            # Gutter-side edge may extend to gutter midpoint at most.
            if layout.gutter_x:
                gutter_mid = (layout.gutter_x[0] + layout.gutter_x[1]) / 2
                x1 = min(x1, gutter_mid) if bb.x1 <= gutter_mid else x1
                x1 = gutter_mid
        elif el.column == Column.RIGHT:
            x1 = margin_x1
            if layout.gutter_x:
                gutter_mid = (layout.gutter_x[0] + layout.gutter_x[1]) / 2
                x0 = gutter_mid
            else:
                x0 = margin_x0
        else:  # SPANNING / SINGLE
            # Pad out from the element's own tight extent, clamped to page
            # margins — NOT an unconditional stretch to full page width.
            # A block only classifies SPANNING because it straddles/ambiguous
            # relative to the gutter; it may still be much narrower than the
            # page. Stretching every spanning element's clip to the full
            # margins regardless of its real width would sweep in whatever
            # left/right-column content happens to sit at the same height,
            # duplicating it into the output.
            x0 = max(margin_x0, bb.x0 - pad)
            x1 = min(margin_x1, bb.x1 + pad)

        padded.append(Bbox(x0, y0, x1, y1))

    # Whitespace-snap: for each element, if its padded bbox intersects a
    # neighbor's padded bbox, walk the offending edge(s) inward until clear.
    # Shrinking is floored at the element's own tight bbox — we only ever
    # give back padding, never cut into the element's actual content, so if
    # two *tight* bboxes already overlap slightly (common: PyMuPDF block
    # bboxes include ascender/descender space) some residual overlap is
    # inherent to the source geometry and can't be snapped away.
    n = len(elements)
    for i in range(n):
        tight_i = elements[i].bbox
        for j in range(n):
            if i == j:
                continue
            bi, bj = padded[i], padded[j]
            guard = 0
            while bi.intersects(bj) and guard < 1000:
                guard += 1
                if (
                    bi.y1 > bj.y0
                    and bi.y0 < bj.y0
                    and bi.y1 <= bj.y1
                    and bi.y1 - step >= tight_i.y1
                ):
                    bi = Bbox(bi.x0, bi.y0, bi.x1, bi.y1 - step)
                elif (
                    bi.y0 < bj.y1
                    and bi.y1 > bj.y1
                    and bi.y0 >= bj.y0
                    and bi.y0 + step <= tight_i.y0
                ):
                    bi = Bbox(bi.x0, bi.y0 + step, bi.x1, bi.y1)
                elif bi.x1 > bj.x0 and bi.x0 < bj.x0 and bi.x1 - step >= tight_i.x1:
                    bi = Bbox(bi.x0, bi.y0, bi.x1 - step, bi.y1)
                elif bi.x0 < bj.x1 and bi.x1 > bj.x1 and bi.x0 + step <= tight_i.x0:
                    bi = Bbox(bi.x0 + step, bi.y0, bi.x1, bi.y1)
                else:
                    break
            padded[i] = bi

    for el, bb in zip(elements, padded):
        el.padded_bbox = bb

    warnings = []
    tol = config.BBOX_OVERLAP_TOLERANCE_PT
    for i in range(n):
        for j in range(i + 1, n):
            padded_overlap = elements[i].padded_bbox.intersects(
                elements[j].padded_bbox, tolerance=tol
            )
            tight_overlap = elements[i].bbox.intersects(elements[j].bbox, tolerance=tol)
            # Only warn when padding introduced overlap that wasn't already
            # present in the source tight bboxes — that's the case snapping
            # is supposed to prevent. Pre-existing tight-bbox overlap is
            # inherent to source extraction, not a defect in our padding.
            if padded_overlap and not tight_overlap:
                warnings.append(
                    f"page {layout.page_no}: padded bbox overlap between element {i} "
                    f"({elements[i].kind.value}, {elements[i].bbox.as_tuple()}) and "
                    f"element {j} ({elements[j].kind.value}, {elements[j].bbox.as_tuple()})"
                )
    return warnings
