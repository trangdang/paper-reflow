"""Regression coverage for _merge_overlapping_same_kind_elements (commit
d20f8b7): two elements sharing column AND kind whose tight bboxes overlap at
all -- even below BBOX_OVERLAP_TOLERANCE_PT -- are one logical unit split by
extraction noise and must be merged; anything differing in column or kind must
be left alone."""

from lib.config import BBOX_OVERLAP_TOLERANCE_PT
from lib.elements import Bbox, Column, Element, Kind
from workflow.layout import _merge_overlapping_same_kind_elements


def _el(kind, column, bbox, text="", refs=None):
    return Element(
        kind=kind,
        page_no=0,
        column=column,
        bbox=Bbox(*bbox),
        text=text,
        source_refs=list(refs or []),
    )


def test_overlapping_same_kind_same_column_merge():
    a = _el(Kind.PARAGRAPH, Column.LEFT, (0, 0, 50, 20), text="hello", refs=["a"])
    b = _el(Kind.PARAGRAPH, Column.LEFT, (0, 18, 50, 40), text="world", refs=["b"])
    out = _merge_overlapping_same_kind_elements([a, b])
    assert len(out) == 1
    merged = out[0]
    assert merged.kind == Kind.PARAGRAPH
    assert merged.column == Column.LEFT
    assert merged.bbox.as_tuple() == (0, 0, 50, 40)
    assert merged.text == "hello\nworld"
    assert merged.source_refs == ["a", "b"]


def test_sub_tolerance_sliver_overlap_still_merges():
    # Overlap smaller than the tolerance the column-only pass respects -- this
    # pass merges anyway because matching kind already rules out over-merging.
    overlap = BBOX_OVERLAP_TOLERANCE_PT / 2
    a = _el(Kind.PARAGRAPH, Column.LEFT, (0, 0, 50, 20))
    b = _el(Kind.PARAGRAPH, Column.LEFT, (0, 20 - overlap, 50, 40))
    out = _merge_overlapping_same_kind_elements([a, b])
    assert len(out) == 1


def test_different_kind_not_merged():
    a = _el(Kind.HEADING, Column.LEFT, (0, 0, 50, 20))
    b = _el(Kind.PARAGRAPH, Column.LEFT, (0, 10, 50, 40))  # overlaps, but different kind
    out = _merge_overlapping_same_kind_elements([a, b])
    assert len(out) == 2


def test_different_column_not_merged():
    a = _el(Kind.PARAGRAPH, Column.LEFT, (0, 0, 50, 20))
    b = _el(Kind.PARAGRAPH, Column.RIGHT, (0, 10, 50, 40))  # overlaps, but different column
    out = _merge_overlapping_same_kind_elements([a, b])
    assert len(out) == 2


def test_non_overlapping_same_kind_not_merged():
    a = _el(Kind.PARAGRAPH, Column.LEFT, (0, 0, 50, 20))
    b = _el(Kind.PARAGRAPH, Column.LEFT, (0, 30, 50, 50))  # gap between them
    out = _merge_overlapping_same_kind_elements([a, b])
    assert len(out) == 2


def test_transitive_chain_collapses_to_one():
    # A overlaps B, B overlaps C, but A and C don't touch -- the fixed-point
    # loop must still collapse all three into a single element.
    a = _el(Kind.PARAGRAPH, Column.LEFT, (0, 0, 50, 20))
    b = _el(Kind.PARAGRAPH, Column.LEFT, (0, 18, 50, 40))
    c = _el(Kind.PARAGRAPH, Column.LEFT, (0, 38, 50, 60))
    out = _merge_overlapping_same_kind_elements([a, b, c])
    assert len(out) == 1
    assert out[0].bbox.as_tuple() == (0, 0, 50, 60)


def test_unrelated_element_left_untouched():
    a = _el(Kind.PARAGRAPH, Column.LEFT, (0, 0, 50, 20))
    b = _el(Kind.PARAGRAPH, Column.LEFT, (0, 18, 50, 40))
    other = _el(Kind.FIGURE, Column.RIGHT, (100, 0, 200, 200))
    out = _merge_overlapping_same_kind_elements([a, b, other])
    assert len(out) == 2
    assert any(e.kind == Kind.FIGURE and e.bbox.as_tuple() == (100, 0, 200, 200) for e in out)
