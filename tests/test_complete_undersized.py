"""Regression coverage for the cross-column guard added to
complete_undersized_elements (commit 9147338): a narrow fragment should only
be pulled across the LEFT/RIGHT gutter when the neighbor is itself an
incomplete fragment. A neighbor that's already column-width-complete is real,
unrelated content and must not be absorbed (which would force an entire column
to SPANNING)."""

from lib.blocks import content_x_extent
from lib.config import ELEMENT_MERGE_SEARCH_GAP_PT, SINGLE_COLUMN_MIN_WIDTH_FRACTION
from lib.elements import Bbox, Column, Element, Kind
from workflow.element_merging import complete_undersized_elements

CONTENT_WIDTH = 600.0
MIN_WIDTH = SINGLE_COLUMN_MIN_WIDTH_FRACTION * CONTENT_WIDTH  # 240
GAP = ELEMENT_MERGE_SEARCH_GAP_PT  # 60


def _el(column, bbox, kind=Kind.PARAGRAPH):
    return Element(kind=kind, page_no=0, column=column, bbox=Bbox(*bbox))


def test_narrow_fragment_not_pulled_into_complete_neighbor_across_gutter():
    # LEFT fragment (100 wide) sits within GAP of a RIGHT neighbor that is
    # already column-width-complete (>= MIN_WIDTH). The guard must leave both
    # standalone rather than merging into a spanning element.
    frag = _el(Column.LEFT, (0, 0, 100, 20))
    complete = _el(Column.RIGHT, (100 + GAP, 0, 100 + GAP + 250, 20))  # width 250 >= 240
    out = complete_undersized_elements([frag, complete], CONTENT_WIDTH)
    assert len(out) == 2
    assert all(e.column != Column.SPANNING for e in out)
    assert {round(e.bbox.width) for e in out} == {100, 250}


def test_two_narrow_halves_across_gutter_merge_to_spanning():
    # Both sides are incomplete fragments -- plausibly one element split by the
    # gutter, so they merge and the result is labeled SPANNING.
    left = _el(Column.LEFT, (0, 0, 100, 20))
    right = _el(Column.RIGHT, (100 + GAP, 0, 100 + GAP + 100, 20))  # width 100 < 240
    out = complete_undersized_elements([left, right], CONTENT_WIDTH)
    assert len(out) == 1
    assert out[0].column == Column.SPANNING
    assert out[0].bbox.width >= MIN_WIDTH


def test_same_column_fragments_merge_without_forcing_spanning():
    # A short trailing line-group merging with its own column's paragraph must
    # keep the column label, not be forced to SPANNING.
    top = _el(Column.LEFT, (0, 0, 100, 20))
    bottom = _el(Column.LEFT, (0, 20, 200, 40))
    out = complete_undersized_elements([top, bottom], CONTENT_WIDTH)
    assert len(out) == 1
    assert out[0].column == Column.LEFT


def test_isolated_small_element_left_standalone():
    # No neighbor within the search radius -- a lone small icon/symbol is not
    # force-inflated.
    lone = _el(Column.LEFT, (0, 0, 30, 30))
    far = _el(Column.LEFT, (0, 500, 30, 530))
    out = complete_undersized_elements([lone, far], CONTENT_WIDTH)
    assert len(out) == 2


def _block(bbox):
    return {"bbox": bbox}


def test_content_x_extent_spans_leftmost_to_rightmost_block():
    # Wide unused margins on either side must not count toward the extent --
    # only the actual leftmost/rightmost block edges matter.
    blocks = [_block((50, 0, 150, 20)), _block((400, 30, 500, 50))]
    assert content_x_extent(blocks, fallback_width=600.0) == 450.0


def test_content_x_extent_falls_back_to_page_width_when_no_blocks():
    assert content_x_extent([], fallback_width=600.0) == 600.0
