"""Bbox primitives underpin every clustering/merging pass, so pin down their
intersect/union/tolerance semantics directly."""

from lib.elements import Bbox


def test_width_and_height():
    b = Bbox(10, 20, 40, 100)
    assert b.width == 30
    assert b.height == 80


def test_overlapping_boxes_intersect():
    a = Bbox(0, 0, 10, 10)
    b = Bbox(5, 5, 15, 15)
    assert a.intersects(b)
    assert b.intersects(a)


def test_disjoint_boxes_do_not_intersect():
    a = Bbox(0, 0, 10, 10)
    b = Bbox(20, 20, 30, 30)
    assert not a.intersects(b)


def test_touching_edges_do_not_intersect():
    # Shared edge only (x1 == other.x0) is not an overlap: the `<=` in
    # intersects treats a touching boundary as disjoint.
    a = Bbox(0, 0, 10, 10)
    b = Bbox(10, 0, 20, 10)
    assert not a.intersects(b)


def test_tolerance_suppresses_a_thin_sliver_overlap():
    # 1pt of overlap on x; a 2pt tolerance treats it as non-overlapping.
    a = Bbox(0, 0, 10, 10)
    b = Bbox(9, 0, 20, 10)
    assert a.intersects(b)
    assert a.intersects(b, tolerance=0.5)
    assert not a.intersects(b, tolerance=2.0)


def test_union_covers_both_boxes():
    a = Bbox(0, 5, 10, 15)
    b = Bbox(3, 0, 20, 8)
    u = a.union(b)
    assert u.as_tuple() == (0, 0, 20, 15)


def test_contains_fully_enclosed_box():
    outer = Bbox(0, 0, 100, 100)
    inner = Bbox(10, 10, 90, 90)
    assert outer.contains(inner)
    assert not inner.contains(outer)


def test_contains_is_reflexive():
    a = Bbox(0, 0, 10, 10)
    assert a.contains(a)


def test_partial_overlap_is_not_containment():
    a = Bbox(0, 0, 100, 50)
    b = Bbox(0, 40, 100, 90)
    assert not a.contains(b)
    assert not b.contains(a)


def test_contains_tolerance_allows_small_protrusion():
    outer = Bbox(0, 0, 100, 100)
    inner = Bbox(-1, -1, 101, 101)  # pokes out 1pt on every edge
    assert not outer.contains(inner)
    assert outer.contains(inner, tolerance=1.5)
