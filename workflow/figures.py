"""Figure/table/caption detection: drawing-rect clustering, ruled-table-line
clustering, dense-small-text-block clustering, and proximity-based caption
matching for each. Figures/tables are matched to captions by proximity, not
containment."""

import re

import fitz

from lib import config
from lib.blocks import _block_bbox, _block_text
from lib.clustering import _cluster_bboxes, _cluster_indices
from lib.elements import Bbox, Column, Element, Kind
from workflow.gutter import classify_or_single

# Table captions are conventionally numbered with roman numerals ("Table
# I"), figures with arabic ("Figure 3") -- accept either after either label.
CAPTION_RE = re.compile(r"^(Figure|Fig\.?|Table)\s*([0-9]+|[IVXLCDM]+)", re.IGNORECASE)
FIGURE_CAPTION_RE = re.compile(r"^(Figure|Fig\.?)\s*([0-9]+|[IVXLCDM]+)", re.IGNORECASE)


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
) -> list[Element]:
    """Fold every element overlapping a detected ruled-table frame -- the
    header row, data rows (however many fragments they landed in), and a
    "Table N" caption sitting just above/below it -- into one TABLE element
    whose bbox is unioned with the rule frame itself. That union is what
    lets the result reach the table's true left/right border position even
    when (as is common) the table has no drawn top border to anchor on: the
    left/right rule extent is still known from the vertical tick marks.

    The merged element's column is left as its first member's -- just a
    placeholder, since the caller (build_page_layout) reclassifies every
    element against the page's column geometry via classify_or_single right
    after this specific pass. That's a one-off correction, not a general
    pattern: none of the other merge steps in workflow/element_merging.py get
    a follow-up reclassification -- they each compute their merged output's
    column directly (inheriting a member's column, or forcing SPANNING on a
    real column-crossing merge)."""
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

        merged_el = Element(
            kind=Kind.TABLE,
            page_no=page_no,
            column=members[0].column,
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


def _build_figure_graphic_elements(
    page: fitz.Page,
    text_blocks: list[dict],
    used_text_idx: set,
    page_no: int,
    is_two_col: bool,
    left_col_x,
    right_col_x,
) -> list[Element]:
    """Build FIGURE/GRAPHIC elements from drawing clusters, with caption
    association, plus dense-text-cluster elements. Mutates used_text_idx in
    place to mark every text block absorbed into a figure/caption/dense
    cluster, so the caller's own text-block pass can skip them.

    Captions are matched by nearest-distance from a shared, undepleted
    candidate pool: a figure is sometimes drawn as several disjoint drawing
    clusters (e.g. two side-by-side sub-diagrams) that all share one caption
    below them. Matching greedily cluster-by-cluster against a pool that
    shrinks as captions get claimed would let the first cluster processed
    permanently claim the caption and starve the rest, which then go hunting
    for the next-nearest caption -- typically an unrelated figure's -- and
    wrongly merge into it.

    A cluster unrelated to a figure can still be its caption's nearest
    drawing cluster overall (e.g. a decorative box sitting just below the
    same caption text a figure sits just above) -- require clusters sharing
    a caption to approach it from the same side (all above, or all below)
    before letting them join the same group.

    Dense clusters of small, closely-packed text blocks (e.g. the many
    individual math-symbol spans of an equation-heavy passage, or a
    borderless table of math symbols with no drawn grid lines) become one
    merged element. Merging them up front is what stops the same content
    being clipped into two overlapping output positions; the kind label is
    secondary. Bias that label toward PARAGRAPH: a dense cluster confined to
    a single column is far more likely an equation-heavy paragraph than a
    table, so only a genuinely *spanning* dense region (which no ordinary
    paragraph produces -- paragraphs are single-column, spanning text is a
    heading or a real full-width table) is left as a TABLE. Ruled tables are
    detected separately via drawn rule lines and are unaffected by this."""
    clusters = _cluster_drawings(page)
    for cluster_bbox in clusters:
        _absorb_contained_labels(cluster_bbox, text_blocks, used_text_idx)

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

    elements: list[Element] = []
    for cap_idx, cluster_idxs in caption_groups.items():
        used_text_idx.add(cap_idx)
        bbox = clusters[cluster_idxs[0]]
        for ci in cluster_idxs[1:]:
            bbox = bbox.union(clusters[ci])
        bbox = bbox.union(_block_bbox(text_blocks[cap_idx]))
        column = classify_or_single(bbox, is_two_col, left_col_x, right_col_x)
        elements.append(Element(kind=Kind.FIGURE, page_no=page_no, column=column, bbox=bbox))

    for ci in uncaptioned:
        bbox = clusters[ci]
        column = classify_or_single(bbox, is_two_col, left_col_x, right_col_x)
        elements.append(Element(kind=Kind.GRAPHIC, page_no=page_no, column=column, bbox=bbox))

    gap = 2 * config.VERTICAL_PAD_PT
    for el in _merge_dense_text_clusters(text_blocks, used_text_idx, gap):
        el.page_no = page_no
        el.column = classify_or_single(el.bbox, is_two_col, left_col_x, right_col_x)
        if el.column != Column.SPANNING:
            el.kind = Kind.PARAGRAPH
        elements.append(el)

    return elements


def _trim_figures_to_content_band(
    elements: list[Element], content_band: tuple[float, float]
) -> None:
    """Trim figure/graphic clusters that poke into the reserved header/footer
    bands so a too-tall drawing bbox doesn't overlap the running-head strip
    (its clip region would otherwise sweep the page number into the figure).
    Restricted to FIGURE/GRAPHIC on purpose: those carry no reflow text
    (they're rendered as vector clips), so trimming the strip crossing a band
    edge can't drop words. Text elements are deliberately left alone -- a
    wide running-head line commonly merges into the first body element, and
    clipping that merged element's top would cut real text out of the render.
    Only the crossing extremity is trimmed, and only when the element also
    reaches into the content region. Mutates each element's bbox in place."""
    band_top, band_bot = content_band
    for el in elements:
        if el.kind not in (Kind.FIGURE, Kind.GRAPHIC):
            continue
        bb = el.bbox
        y0 = max(bb.y0, band_top) if bb.y1 > band_top else bb.y0
        y1 = min(bb.y1, band_bot) if bb.y0 < band_bot else bb.y1
        el.bbox = Bbox(bb.x0, y0, bb.x1, y1)


def _rect_gap(a: Bbox, b: Bbox) -> float:
    """Chebyshev-style gap between two bboxes: 0 if they overlap/touch on
    both axes, otherwise the larger of the x- and y-axis separations."""
    dx = max(a.x0 - b.x1, b.x0 - a.x1, 0.0)
    dy = max(a.y0 - b.y1, b.y0 - a.y1, 0.0)
    return max(dx, dy)


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
