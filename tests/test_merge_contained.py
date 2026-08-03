"""Regression coverage for merge_contained_elements: an element whose tight
bbox sits fully inside another element's bbox is duplicated content (clip-based
rendering draws the container's whole region), so it must be absorbed into the
container regardless of column or kind. The real-paper trigger is an undersized
SPANNING fragment growing into a blob that encloses a separate complete-width
RIGHT paragraph the same-column/same-kind passes miss (differing columns)."""

from lib.config import BBOX_OVERLAP_TOLERANCE_PT
from lib.elements import Bbox, Column, Element, Kind
from workflow.element_merging import merge_contained_elements


def _el(kind, column, bbox, text="", refs=None):
    return Element(
        kind=kind,
        page_no=0,
        column=column,
        bbox=Bbox(*bbox),
        text=text,
        source_refs=list(refs or []),
    )


def test_contained_different_column_merges_into_container():
    # The page-15 case: SPANNING blob fully encloses a complete-width RIGHT
    # paragraph. Container column/bbox win; text and refs combine.
    outer = _el(Kind.PARAGRAPH, Column.SPANNING, (312, 645, 563, 752), text="outer", refs=["o"])
    inner = _el(Kind.PARAGRAPH, Column.RIGHT, (317, 662, 558, 703), text="inner", refs=["i"])
    out = merge_contained_elements([outer, inner])
    assert len(out) == 1
    merged = out[0]
    assert merged.column == Column.SPANNING
    assert merged.bbox.as_tuple() == (312, 645, 563, 752)
    assert merged.text == "outer\ninner"
    assert merged.source_refs == ["o", "i"]


def test_kind_follows_merge_priority():
    # A figure containing a caption-sized paragraph keeps the higher-priority
    # FIGURE kind.
    outer = _el(Kind.PARAGRAPH, Column.LEFT, (0, 0, 100, 100))
    inner = _el(Kind.FIGURE, Column.LEFT, (10, 10, 90, 90))
    out = merge_contained_elements([outer, inner])
    assert len(out) == 1
    assert out[0].kind == Kind.FIGURE
    assert out[0].bbox.as_tuple() == (0, 0, 100, 100)


def test_sub_tolerance_protrusion_still_contained():
    # Inner box pokes past the container by less than the tolerance -- still
    # absorbed (PyMuPDF ascender/descender box padding).
    eps = BBOX_OVERLAP_TOLERANCE_PT / 2
    outer = _el(Kind.PARAGRAPH, Column.LEFT, (0, 0, 100, 100))
    inner = _el(Kind.PARAGRAPH, Column.LEFT, (-eps, 10, 100 + eps, 90))
    out = merge_contained_elements([outer, inner])
    assert len(out) == 1


def test_partial_overlap_not_merged():
    # Overlapping but neither contains the other -- this pass must leave them
    # alone (that's the overlap passes' job, guarded against cascading).
    a = _el(Kind.PARAGRAPH, Column.LEFT, (0, 0, 100, 50))
    b = _el(Kind.PARAGRAPH, Column.LEFT, (0, 40, 100, 90))
    out = merge_contained_elements([a, b])
    assert len(out) == 2


def test_disjoint_not_merged():
    a = _el(Kind.PARAGRAPH, Column.LEFT, (0, 0, 50, 20))
    b = _el(Kind.PARAGRAPH, Column.RIGHT, (100, 0, 150, 20))
    out = merge_contained_elements([a, b])
    assert len(out) == 2


def test_nested_containment_collapses_to_one():
    # A contains B contains C -- fixed-point loop collapses all three.
    a = _el(Kind.PARAGRAPH, Column.SPANNING, (0, 0, 100, 100))
    b = _el(Kind.PARAGRAPH, Column.LEFT, (10, 10, 90, 90))
    c = _el(Kind.PARAGRAPH, Column.RIGHT, (20, 20, 80, 80))
    out = merge_contained_elements([a, b, c])
    assert len(out) == 1
    assert out[0].bbox.as_tuple() == (0, 0, 100, 100)
    assert out[0].column == Column.SPANNING
