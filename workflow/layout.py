"""Per-page layout detection: gutter/column split, block classification,
element grouping, and bbox padding/whitespace-snapping."""

import re
import statistics

import fitz

from lib import config
from lib.blocks import _block_bbox, _block_text, _content_x_extent
from lib.clustering import _cluster_bboxes, _cluster_indices
from lib.elements import Bbox, Column, Element, Kind, PageLayout
from workflow.content_bands import get_text_blocks
from workflow.gutter import classify_block, detect_gutter, pin_gutter_width

# Table captions are conventionally numbered with roman numerals ("Table
# I"), figures with arabic ("Figure 3") -- accept either after either label.
CAPTION_RE = re.compile(r"^(Figure|Fig\.?|Table)\s*([0-9]+|[IVXLCDM]+)", re.IGNORECASE)
FIGURE_CAPTION_RE = re.compile(r"^(Figure|Fig\.?)\s*([0-9]+|[IVXLCDM]+)", re.IGNORECASE)


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


def _merge_dense_text_clusters(
    text_blocks: list[dict], used: set, gap: float, small_dim_pt: float = 40.0
) -> list[Element]:
    """Borderless tables (a grid of many individual math-symbol spans with no
    drawn rule lines) fragment into dozens of tiny separate text blocks that
    the normal per-block classification leaves as distinct, closely-packed
    elements — after padding these overlap and the same content gets clipped
    into two output positions (duplicated text). Cluster small, mutually
    close blocks into one element up front so they render exactly once."""
    small_idx = [
        i
        for i, b in enumerate(text_blocks)
        if i not in used
        and (lambda bb: bb.width <= small_dim_pt and bb.height <= small_dim_pt)(_block_bbox(b))
    ]
    if not small_idx:
        return []

    bboxes = [_block_bbox(text_blocks[i]) for i in small_idx]
    groups = _cluster_indices(bboxes, gap)

    merged: list[Element] = []
    for group in groups:
        if len(group) < 2:
            continue
        orig_indices = [small_idx[g] for g in group]
        blocks = [text_blocks[i] for i in orig_indices]
        bbox = _block_bbox(blocks[0])
        text_parts = [_block_text(blocks[0])]
        for b in blocks[1:]:
            bbox = bbox.union(_block_bbox(b))
            text_parts.append(_block_text(b))
        for i in orig_indices:
            used.add(i)
        merged.append(
            Element(
                kind=Kind.TABLE,
                page_no=-1,
                column=Column.SPANNING,
                bbox=bbox,
                text=" ".join(text_parts),
            )
        )
    return merged


def _table_rule_regions(page: fitz.Page) -> list[Bbox]:
    """Ruled table borders show up in get_drawings as degenerate stroked
    rects -- zero width (vertical rules/ticks) or zero height (horizontal
    rules) -- not filled area, so _cluster_drawings' area-based clustering
    never sees them. Cluster these line fragments by proximity into whole
    -table frames instead. A cluster must contain several fragments (tick
    marks plus full-width separator rules) to count as a ruled table rather
    than a stray underline or a box rule used decoratively elsewhere."""
    lines = []
    for d in page.get_drawings():
        r = d.get("rect")
        if r is None:
            continue
        if (r.width == 0) == (r.height == 0):
            continue  # keep only genuine one-dimensional rule segments
        lines.append(Bbox(r.x0, r.y0, r.x1, r.y1))
    if not lines:
        return []

    groups = _cluster_indices(lines, config.TABLE_RULE_CLUSTER_GAP_PT)
    regions = []
    for group in groups:
        if len(group) < config.TABLE_RULE_MIN_LINES:
            continue
        # A decorative box outline (4 lines: one per side) can bridge into a
        # neighboring box within the same proximity gap and rack up enough
        # total fragments to pass the count check above, but it never has
        # more than a couple of distinct vertical rule positions. A real
        # ruled table has one vertical tick per column boundary, so require
        # several distinct vertical x-positions -- a shape a handful of box
        # outlines can't produce even when bridged together.
        vertical_xs = {round(lines[i].x0, 1) for i in group if lines[i].width == 0}
        if len(vertical_xs) < config.TABLE_RULE_MIN_DISTINCT_VERTICALS:
            continue
        bbox = lines[group[0]]
        for idx in group[1:]:
            bbox = bbox.union(lines[idx])
        # A decorative box drawn around a whole example/equation (2 long
        # horizontal lines: top + bottom) can still rack up enough fragments
        # and distinct vertical positions to pass the checks above if
        # unrelated short marks (underlines, diacritics on math symbols)
        # happen to fall within clustering distance along its height -- a
        # real ruled table has multiple full-width separator rules (header,
        # inter-row, bottom), not just a top/bottom pair, so require several
        # distinct full-width horizontal rules before trusting the region.
        full_width_ys = {
            round(lines[i].y0, 1)
            for i in group
            if lines[i].height == 0
            and lines[i].width >= config.TABLE_RULE_FULL_WIDTH_FRACTION * bbox.width
        }
        if len(full_width_ys) < config.TABLE_RULE_MIN_FULL_WIDTH_LINES:
            continue
        regions.append(bbox)
    return regions


def _merge_table_rule_regions(
    elements: list[Element],
    page: fitz.Page,
    page_no: int,
    is_two_col: bool,
    left_col_x,
    right_col_x,
) -> list[Element]:
    """Fold every element overlapping a detected ruled-table frame -- the
    header row, data rows (however many fragments they landed in), and a
    "Table N" caption sitting just above/below it -- into one TABLE element
    whose bbox is unioned with the rule frame itself. That union is what
    lets the result reach the table's true left/right border position even
    when (as is common) the table has no drawn top border to anchor on: the
    left/right rule extent is still known from the vertical tick marks."""
    regions = _table_rule_regions(page)
    if not regions:
        return elements

    for region in regions:
        member_ids = set()
        members = [el for el in elements if el.bbox.intersects(region)]
        member_ids.update(id(el) for el in members)

        # A "Table N" caption often sits just outside the ruled frame (most
        # commonly above it, since this table style has no top border to
        # separate the caption from) -- absorb it the same way figure
        # captions are absorbed, by proximity rather than overlap.
        for el in elements:
            if id(el) in member_ids:
                continue
            if not CAPTION_RE.match(el.text.strip()):
                continue
            if el.bbox.y1 <= region.y0:
                dist = region.y0 - el.bbox.y1
            elif el.bbox.y0 >= region.y1:
                dist = el.bbox.y0 - region.y1
            else:
                dist = 0.0
            if dist <= config.CAPTION_MAX_DISTANCE_PT:
                members.append(el)
                member_ids.add(id(el))

        if not members:
            continue

        merged_bbox = region
        text_parts = []
        source_refs = []
        for el in members:
            merged_bbox = merged_bbox.union(el.bbox)
            if el.text:
                text_parts.append(el.text)
            source_refs.extend(el.source_refs)

        column = (
            classify_block(merged_bbox, left_col_x, right_col_x) if is_two_col else Column.SINGLE
        )
        merged_el = Element(
            kind=Kind.TABLE,
            page_no=page_no,
            column=column,
            bbox=merged_bbox,
            text="\n".join(text_parts),
            source_refs=source_refs,
        )
        elements = [el for el in elements if id(el) not in member_ids] + [merged_el]

    return elements


def _find_invisible_white_fill_artifacts(drawings: list[dict], page_rect: fitz.Rect) -> set[int]:
    """Identify stroke-free, pure-white-filled rects that are stale leftovers (e.g. from a
    boxed-equation LaTeX template), not intentional page content. A white fill alone isn't enough
    to tell: real boxed content (e.g. an "Example N" box) is commonly built from a white
    background rect plus separate stroked border lines, and that background rect's bbox is what
    the layout pipeline elsewhere relies on to delineate the box -- treating every white fill as
    invisible would strip that too and let unrelated neighboring content (e.g. a nearby figure)
    bleed into the box's element.

    What actually marks a rect as a stale artifact rather than a real, once-off background: the
    same LaTeX macro stamps out one per equation instance regardless of whether that instance is
    actually meant to be boxed, so they recur at an identical size across the page -- and because
    their position is computed relative to the (unboxed) equation rather than the page, at least
    one copy typically ends up mispositioned off the physical page. A real content background is
    both on-page and unique in size (no reason for two unrelated boxes to be pixel-identical).

    Off-page positioning isn't the only tell, though: sometimes every stamped-out copy lands on
    the page, just jittered relative to each other (e.g. offset a few dozen points horizontally
    between equation instances). Two same-size rects that *overlap* each other are just as
    telling as one being off-page -- genuine distinct content boxes of identical size don't get
    stacked on top of one another, since that would just hide one behind the other."""
    candidates = []
    for i, d in enumerate(drawings):
        r = d.get("rect")
        if r is None or r.width <= 0 or r.height <= 0:
            continue
        if d.get("color") is not None:
            continue  # has a stroke, so it's visible
        fill = d.get("fill")
        if fill is None or not all(c >= config.WHITE_FILL_MIN_COMPONENT for c in fill):
            continue
        candidates.append((i, r))

    by_size: dict[tuple[float, float], list[tuple[int, fitz.Rect]]] = {}
    for i, r in candidates:
        by_size.setdefault((round(r.width, 1), round(r.height, 1)), []).append((i, r))

    def is_off_page(r: fitz.Rect) -> bool:
        return (
            r.x0 < page_rect.x0 or r.y0 < page_rect.y0 or r.x1 > page_rect.x1 or r.y1 > page_rect.y1
        )

    def any_pair_overlaps(items: list[tuple[int, fitz.Rect]]) -> bool:
        return any(a.intersects(b) for i, (_, a) in enumerate(items) for _, b in items[i + 1 :])

    artifact_indices: set[int] = set()
    for items in by_size.values():
        if len(items) < 2:
            continue
        if any(is_off_page(r) for _, r in items) or any_pair_overlaps(items):
            artifact_indices.update(i for i, _ in items)
    return artifact_indices


def _cluster_drawings(page: fitz.Page) -> list[Bbox]:
    """Greedy union-find style clustering of drawing rects by proximity."""
    drawings = page.get_drawings()
    artifact_idx = _find_invisible_white_fill_artifacts(drawings, page.rect)
    rects = []
    for i, d in enumerate(drawings):
        r = d.get("rect")
        if r is None:
            continue
        if r.width <= 0 or r.height <= 0:
            continue
        if i in artifact_idx:
            continue
        rects.append(Bbox(r.x0, r.y0, r.x1, r.y1))

    clusters = _cluster_bboxes(rects, config.FIGURE_CLUSTER_GAP_PT)
    return [c for c in clusters if c.width * c.height >= config.MIN_DRAWING_CLUSTER_AREA_PT2]


def _absorb_contained_labels(cluster_bbox: Bbox, text_blocks: list[dict], used: set) -> Bbox:
    """Fold text blocks that sit visually inside a figure's vector-art region
    (e.g. axis/equation labels layered on top of the drawing) into the figure
    element, so they don't linger as separate paragraph elements that falsely
    register as overlapping the figure's clip region."""
    bbox = cluster_bbox
    for idx, b in enumerate(text_blocks):
        if idx in used:
            continue
        bb = _block_bbox(b)
        area = bb.width * bb.height
        if area <= 0:
            continue
        ix0, iy0 = max(bb.x0, bbox.x0), max(bb.y0, bbox.y0)
        ix1, iy1 = min(bb.x1, bbox.x1), min(bb.y1, bbox.y1)
        inter_area = max(0, ix1 - ix0) * max(0, iy1 - iy0)
        if inter_area / area >= 0.8:
            used.add(idx)
    return bbox


def _find_caption(cluster_bbox: Bbox, text_blocks: list[dict], used: set) -> dict | None:
    best = None
    best_dist = None
    for idx, b in enumerate(text_blocks):
        if idx in used:
            continue
        text = _block_text(b)
        if not CAPTION_RE.match(text.strip()):
            continue
        bb = _block_bbox(b)
        # A caption must horizontally relate to its figure, not just sit
        # within range vertically -- otherwise a caption near the bottom of
        # one column can read as "close" to a drawing cluster at the top of
        # the far column purely because both happen to be near the same
        # row, and wrongly get matched across columns.
        x_gap = max(cluster_bbox.x0 - bb.x1, bb.x0 - cluster_bbox.x1, 0.0)
        if x_gap > config.CAPTION_MAX_X_GAP_PT:
            continue
        # below
        if bb.y0 >= cluster_bbox.y1:
            dist = bb.y0 - cluster_bbox.y1
        # above
        elif bb.y1 <= cluster_bbox.y0:
            dist = cluster_bbox.y0 - bb.y1
        else:
            continue
        if dist <= config.CAPTION_MAX_DISTANCE_PT and (best_dist is None or dist < best_dist):
            best, best_dist = (idx, b), dist
    return best


def _rect_gap(a: Bbox, b: Bbox) -> float:
    """Chebyshev-style gap between two bboxes: 0 if they overlap/touch on
    both axes, otherwise the larger of the x- and y-axis separations."""
    dx = max(a.x0 - b.x1, b.x0 - a.x1, 0.0)
    dy = max(a.y0 - b.y1, b.y0 - a.y1, 0.0)
    return max(dx, dy)


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


def _merge_orphan_figure_captions(elements: list[Element]) -> list[Element]:
    """A diagram baked into a scanned/rasterized page background leaves no
    vector drawing paths for _cluster_drawings to key off, so it never gets a
    FIGURE element from the cluster+caption pass above. What's left is just
    the diagram's own in-picture OCR text (axis/path labels) -- short, bold,
    large-font fragments that _is_heading reads as a HEADING -- sitting well
    above its "Figure N" caption, which lands as an ordinary PARAGRAPH with
    nothing in between to explain the gap. Pair a HEADING with the nearest
    later figure caption horizontally aligned with it, as long as no other
    element sits visually between them, into one FIGURE element unioning
    both bboxes -- that's what lets the figure's clip region cover the
    actual (undetected) artwork between the label and its caption, not just
    the label text itself.

    Matching is by x-position, not the assigned Column: the label (wide,
    reaching toward the gutter) and its narrower caption commonly land in
    different Column buckets from classify_block's fractional-overlap test
    even though they're clearly the same figure."""
    consumed: set[int] = set()
    result: list[Element] = []
    for i, el in enumerate(elements):
        if el.kind != Kind.HEADING:
            result.append(el)
            continue
        best: tuple[int, Element] | None = None
        for j, other in enumerate(elements):
            if j == i or j in consumed:
                continue
            if other.kind != Kind.PARAGRAPH:
                continue
            if not other.text or not FIGURE_CAPTION_RE.match(other.text.strip()):
                continue
            if other.bbox.y0 < el.bbox.y1:
                continue
            x_gap = max(el.bbox.x0 - other.bbox.x1, other.bbox.x0 - el.bbox.x1, 0.0)
            if x_gap > config.CAPTION_MAX_X_GAP_PT:
                continue
            if best is None or other.bbox.y0 < best[1].bbox.y0:
                best = (j, other)
        if best is None:
            result.append(el)
            continue
        j, cap = best
        if cap.bbox.y0 - el.bbox.y1 > config.ORPHAN_FIGURE_LABEL_MAX_GAP_PT:
            result.append(el)
            continue
        union_x0, union_x1 = min(el.bbox.x0, cap.bbox.x0), max(el.bbox.x1, cap.bbox.x1)
        gap_region = Bbox(union_x0, el.bbox.y1, union_x1, cap.bbox.y0)
        between = any(
            k != i and k != j and k not in consumed and other.bbox.intersects(gap_region)
            for k, other in enumerate(elements)
        )
        if between:
            result.append(el)
            continue
        bbox = el.bbox.union(cap.bbox)
        result.append(Element(kind=Kind.FIGURE, page_no=el.page_no, column=el.column, bbox=bbox))
        consumed.add(j)
    return [el for i, el in enumerate(result) if i not in consumed]


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

    elements: list[Element] = []
    used_text_idx: set = set()

    # Figures/graphics from drawing clusters, with caption association.
    # Captions are matched by nearest-distance from a shared, undepleted
    # candidate pool: a figure is sometimes drawn as several disjoint
    # drawing clusters (e.g. two side-by-side sub-diagrams) that all share
    # one caption below them. Matching greedily cluster-by-cluster against
    # a pool that shrinks as captions get claimed would let the first
    # cluster processed permanently claim the caption and starve the rest,
    # which then go hunting for the next-nearest caption -- typically an
    # unrelated figure's -- and wrongly merge into it.
    clusters = _cluster_drawings(page)
    for cluster_bbox in clusters:
        _absorb_contained_labels(cluster_bbox, text_blocks, used_text_idx)

    # A cluster unrelated to a figure can still be its caption's nearest
    # drawing cluster overall (e.g. a decorative box sitting just below the
    # same caption text a figure sits just above) -- require clusters
    # sharing a caption to approach it from the same side (all above, or
    # all below) before letting them join the same group.
    caption_groups: dict[int, list[int]] = {}
    caption_side: dict[int, str] = {}
    uncaptioned: list[int] = []
    for ci, cluster_bbox in enumerate(clusters):
        caption = _find_caption(cluster_bbox, text_blocks, used_text_idx)
        if caption is None:
            uncaptioned.append(ci)
            continue
        cap_idx, cap_block = caption
        side = "above" if cluster_bbox.y1 <= _block_bbox(cap_block).y0 else "below"
        if cap_idx in caption_groups and caption_side[cap_idx] != side:
            uncaptioned.append(ci)
            continue
        caption_groups.setdefault(cap_idx, []).append(ci)
        caption_side[cap_idx] = side

    for cap_idx, cluster_idxs in caption_groups.items():
        used_text_idx.add(cap_idx)
        bbox = clusters[cluster_idxs[0]]
        for ci in cluster_idxs[1:]:
            bbox = bbox.union(clusters[ci])
        bbox = bbox.union(_block_bbox(text_blocks[cap_idx]))
        column = classify_block(bbox, left_col_x, right_col_x) if is_two_col else Column.SINGLE
        elements.append(Element(kind=Kind.FIGURE, page_no=page_no, column=column, bbox=bbox))

    for ci in uncaptioned:
        bbox = clusters[ci]
        column = classify_block(bbox, left_col_x, right_col_x) if is_two_col else Column.SINGLE
        elements.append(Element(kind=Kind.GRAPHIC, page_no=page_no, column=column, bbox=bbox))

    # Dense clusters of small, closely-packed text blocks (e.g. the many
    # individual math-symbol spans of an equation-heavy passage, or a
    # borderless table of math symbols with no drawn grid lines) -> one
    # merged element. Merging them up front is what stops the same content
    # being clipped into two overlapping output positions; the kind label is
    # secondary. Bias that label toward PARAGRAPH: a dense cluster confined to
    # a single column is far more likely an equation-heavy paragraph than a
    # table, so only a genuinely *spanning* dense region (which no ordinary
    # paragraph produces -- paragraphs are single-column, spanning text is a
    # heading or a real full-width table) is left as a TABLE. Ruled tables are
    # detected separately via drawn rule lines and are unaffected by this.
    gap = 2 * config.VERTICAL_PAD_PT
    for el in _merge_dense_text_clusters(text_blocks, used_text_idx, gap):
        el.page_no = page_no
        el.column = (
            classify_block(el.bbox, left_col_x, right_col_x) if is_two_col else Column.SINGLE
        )
        if el.column != Column.SPANNING:
            el.kind = Kind.PARAGRAPH
        elements.append(el)

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

    # Trim figure/graphic clusters that poke into the reserved header/footer
    # bands so a too-tall drawing bbox doesn't overlap the running-head strip
    # (its clip region would otherwise sweep the page number into the figure).
    # Restricted to FIGURE/GRAPHIC on purpose: those carry no reflow text
    # (they're rendered as vector clips), so trimming the strip crossing a band
    # edge can't drop words. Text elements are deliberately left alone -- a
    # wide running-head line commonly merges into the first body element, and
    # clipping that merged element's top would cut real text out of the render.
    # Only the crossing extremity is trimmed, and only when the element also
    # reaches into the content region.
    if content_band is not None:
        band_top, band_bot = content_band
        for el in elements:
            if el.kind not in (Kind.FIGURE, Kind.GRAPHIC):
                continue
            bb = el.bbox
            y0 = max(bb.y0, band_top) if bb.y1 > band_top else bb.y0
            y1 = min(bb.y1, band_bot) if bb.y0 < band_bot else bb.y1
            el.bbox = Bbox(bb.x0, y0, bb.x1, y1)

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
