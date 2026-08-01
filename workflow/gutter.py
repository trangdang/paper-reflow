"""Two-column gutter detection and column-membership classification."""

import fitz

from lib import config
from lib.blocks import block_bbox, content_x_range
from lib.elements import Bbox, Column


def detect_gutter(
    page: fitz.Page, blocks: list[dict]
) -> tuple[bool, tuple | None, tuple | None, tuple | None]:
    """Detect the two-column gutter for a page.

    Returns (is_two_column, left_col_x, right_col_x, gutter_x).
    """
    if not blocks:
        return False, None, None, None

    bboxes = [block_bbox(b) for b in blocks]
    content_x0, content_x1 = content_x_range(blocks)
    content_width = content_x1 - content_x0
    if content_width <= 0:
        return False, None, None, None

    # A block only "votes" on the gutter location if it's both narrow AND
    # sits entirely within one half of the content region. Width alone isn't
    # enough: a horizontally-centered block (e.g. an author list) can be
    # narrower than the width threshold yet still straddle the true gutter,
    # which would corrupt the occupancy histogram right where the gap is.
    content_center = (content_x0 + content_x1) / 2
    half_tolerance = 5.0
    narrow = [
        bb
        for bb in bboxes
        if bb.width <= config.NARROW_BLOCK_MAX_FRACTION * content_width
        and (bb.x1 <= content_center + half_tolerance or bb.x0 >= content_center - half_tolerance)
    ]
    if not narrow:
        return False, None, None, None

    # Build an occupancy histogram over x using narrow blocks only.
    resolution = 2  # points per bucket
    n_buckets = max(1, int(content_width // resolution) + 1)
    occupancy = [0] * n_buckets

    def bucket_range(x0, x1):
        b0 = int((x0 - content_x0) // resolution)
        b1 = int((x1 - content_x0) // resolution)
        return max(0, b0), min(n_buckets - 1, b1)

    for bb in narrow:
        b0, b1 = bucket_range(bb.x0, bb.x1)
        for i in range(b0, b1 + 1):
            occupancy[i] += bb.height

    # Threshold as a fraction of the busiest bucket's occupancy, not the
    # page's full vertical content extent -- a fixed height bar assumes every
    # real column is packed with text top-to-bottom, which breaks whenever a
    # column holds a large figure or a sparse table (real content, but not
    # narrow text blocks): the column's own text can fall far short of that
    # bar and the whole column gets misread as the gutter. Calibrating against
    # the page's own densest column self-corrects for how much text actually
    # exists, regardless of what non-text content shares the page.
    threshold = (1.0 - config.GUTTER_COVERAGE_MIN_FRACTION) * max(occupancy)
    best_gap = None
    i = 0
    while i < n_buckets:
        if occupancy[i] <= threshold:
            j = i
            while j < n_buckets and occupancy[j] <= threshold:
                j += 1
            gap_x0 = content_x0 + i * resolution
            gap_x1 = content_x0 + j * resolution
            gap_width = gap_x1 - gap_x0
            # Prefer gaps roughly centered in the content region, and wide enough.
            if gap_width >= config.GUTTER_MIN_WIDTH_PT:
                center = (gap_x0 + gap_x1) / 2
                content_center = (content_x0 + content_x1) / 2
                centrality = abs(center - content_center) / (content_width / 2)
                if centrality < 0.6:  # not too close to either edge
                    if best_gap is None or gap_width > best_gap[2]:
                        best_gap = (gap_x0, gap_x1, gap_width)
            i = j
        else:
            i += 1

    if best_gap is None:
        return False, None, None, None

    gap_x0, gap_x1, _ = best_gap
    left_col_x = (content_x0, gap_x0)
    right_col_x = (gap_x1, content_x1)
    gutter_x = (gap_x0, gap_x1)
    return True, left_col_x, right_col_x, gutter_x


def pin_gutter_width(
    left_col_x: tuple,
    right_col_x: tuple,
    gutter_x: tuple,
    gutter_width_override: float,
) -> tuple[tuple, tuple, tuple]:
    """Re-center a page's detected gutter but pin its width to a document-wide
    consensus value.

    Per-page gutter detection is noisy: a page whose narrow-block occupancy
    histogram happens to be sparser near the gutter (e.g. fewer/shorter
    math-symbol fragments straddling the column split) measures a wider
    "empty" gap than a page with denser content there, even though the
    physical column layout is identical everywhere in the document.

    Returns (left_col_x, right_col_x, gutter_x).
    """
    center = (gutter_x[0] + gutter_x[1]) / 2
    half = gutter_width_override / 2
    gutter_x = (center - half, center + half)
    left_col_x = (left_col_x[0], gutter_x[0])
    right_col_x = (gutter_x[1], right_col_x[1])
    return left_col_x, right_col_x, gutter_x


def classify_block(bbox: Bbox, left_col_x, right_col_x) -> Column:
    left_overlap = max(0, min(bbox.x1, left_col_x[1]) - max(bbox.x0, left_col_x[0]))
    right_overlap = max(0, min(bbox.x1, right_col_x[1]) - max(bbox.x0, right_col_x[0]))
    width = bbox.width if bbox.width > 0 else 1e-6

    if left_overlap / width >= config.COLUMN_MEMBERSHIP_MIN_FRACTION:
        return Column.LEFT
    if right_overlap / width >= config.COLUMN_MEMBERSHIP_MIN_FRACTION:
        return Column.RIGHT
    # Ambiguous / straddling / wide -> default to SPANNING (safer failure mode).
    return Column.SPANNING


def classify_or_single(bbox: Bbox, is_two_col: bool, left_col_x, right_col_x) -> Column:
    """classify_block, but for a single-column page every block is just the
    whole-width Column.SINGLE rather than a LEFT/RIGHT/SPANNING split that
    doesn't apply."""
    return classify_block(bbox, left_col_x, right_col_x) if is_two_col else Column.SINGLE
