"""Bbox padding / whitespace-snapping (Milestone 4): expands each element's
tight bbox outward toward its column's real margins, then whitespace-snaps
any resulting overlaps between neighbors' padded boxes inward."""

import fitz

from lib import config
from lib.elements import Bbox, Column, Kind, PageLayout


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
    clearance = config.CLIP_GUTTER_CLEARANCE_PT

    def _y_overlaps(a: Bbox, b: Bbox) -> bool:
        return a.y1 > b.y0 and b.y1 > a.y0

    def _opposite_content_edge(bb: Bbox, opposite: Column, is_left: bool) -> float | None:
        """Nearest tight gutter-side edge of any vertically overlapping element
        in the opposite column -- the right column's leftmost x0 (for a LEFT
        element) or the left column's rightmost x1 (for a RIGHT element). None
        if no opposite-column element shares this element's vertical span."""
        edges = [
            (o.bbox.x0 if is_left else o.bbox.x1)
            for o in elements
            if o.column == opposite and _y_overlaps(bb, o.bbox)
        ]
        if not edges:
            return None
        return min(edges) if is_left else max(edges)

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
            # Inner (gutter-side) edge sits at the gutter midpoint, but is never
            # pulled inside the element's own content: a block whose text edges
            # past the midpoint into the gutter (e.g. a subheading flush at the
            # column margin) must stay fully covered by its clip, or its
            # gutter-side glyphs get cut off. Snapping below still gives padding
            # back down to the tight bbox when two columns' clips collide.
            if layout.gutter_x:
                gutter_mid = (layout.gutter_x[0] + layout.gutter_x[1]) / 2
                x1 = max(gutter_mid, bb.x1)
            else:
                x1 = margin_x1
            # Never let the clip edge reach the right column's real content
            # (see CLIP_GUTTER_CLEARANCE_PT) -- but never cut this element's own.
            edge = _opposite_content_edge(bb, Column.RIGHT, is_left=True)
            if edge is not None:
                x1 = max(min(x1, edge - clearance), bb.x1)
        elif el.column == Column.RIGHT:
            x1 = margin_x1
            if layout.gutter_x:
                gutter_mid = (layout.gutter_x[0] + layout.gutter_x[1]) / 2
                x0 = min(gutter_mid, bb.x0)
            else:
                x0 = margin_x0
            edge = _opposite_content_edge(bb, Column.LEFT, is_left=False)
            if edge is not None:
                x0 = min(max(x0, edge + clearance), bb.x0)
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
    #
    # Each inward step is at most SNAP_STEP_PT, but the final step clamps
    # exactly to the tight-bbox edge rather than bailing when a whole step
    # would overshoot it — otherwise up to nearly a full step of padding
    # could be left un-given-back on each side, and two nearly-touching
    # same-column blocks would keep a residual padded overlap (approaching
    # 2*SNAP_STEP_PT) that snapping is meant to eliminate.
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
                if bi.y1 > bj.y0 and bi.y0 < bj.y0 and bi.y1 <= bj.y1 and bi.y1 > tight_i.y1:
                    bi = Bbox(bi.x0, bi.y0, bi.x1, max(bi.y1 - step, tight_i.y1))
                elif bi.y0 < bj.y1 and bi.y1 > bj.y1 and bi.y0 >= bj.y0 and bi.y0 < tight_i.y0:
                    bi = Bbox(bi.x0, min(bi.y0 + step, tight_i.y0), bi.x1, bi.y1)
                elif bi.x1 > bj.x0 and bi.x0 < bj.x0 and bi.x1 > tight_i.x1:
                    bi = Bbox(bi.x0, bi.y0, max(bi.x1 - step, tight_i.x1), bi.y1)
                elif bi.x0 < bj.x1 and bi.x1 > bj.x1 and bi.x0 < tight_i.x0:
                    bi = Bbox(min(bi.x0 + step, tight_i.x0), bi.y0, bi.x1, bi.y1)
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
