"""Regression coverage for _find_invisible_white_fill_artifacts (commit
9147338): stroke-free, near-white fills that recur at an identical size with at
least one copy off the physical page are stale LaTeX-template artifacts and
should be flagged; a unique on-page white background (real boxed content) and
any stroked/non-white rect must not be."""

import fitz

from lib.config import WHITE_FILL_MIN_COMPONENT
from workflow.layout import _find_invisible_white_fill_artifacts

PAGE = fitz.Rect(0, 0, 600, 800)


def _drawing(rect, fill=(1.0, 1.0, 1.0), color=None):
    return {"rect": fitz.Rect(*rect), "fill": fill, "color": color}


def test_recurring_white_fill_with_off_page_copy_is_flagged():
    drawings = [
        _drawing((100, 100, 200, 150)),  # on-page copy
        _drawing((100, -400, 200, -350)),  # identical size, off the top of the page
    ]
    assert _find_invisible_white_fill_artifacts(drawings, PAGE) == {0, 1}


def test_unique_on_page_white_background_is_not_flagged():
    drawings = [_drawing((100, 100, 300, 400))]
    assert _find_invisible_white_fill_artifacts(drawings, PAGE) == set()


def test_two_identical_but_both_on_page_are_not_flagged():
    # Needs at least one off-page copy to be considered a mispositioned stamp.
    drawings = [
        _drawing((100, 100, 200, 150)),
        _drawing((300, 100, 400, 150)),
    ]
    assert _find_invisible_white_fill_artifacts(drawings, PAGE) == set()


def test_stroked_rect_is_not_a_candidate():
    # A visible border (color set) means it's real content, even off-page.
    drawings = [
        _drawing((100, 100, 200, 150), color=(0, 0, 0)),
        _drawing((100, -400, 200, -350), color=(0, 0, 0)),
    ]
    assert _find_invisible_white_fill_artifacts(drawings, PAGE) == set()


def test_non_white_fill_is_not_a_candidate():
    grey = (WHITE_FILL_MIN_COMPONENT - 0.1,) * 3
    drawings = [
        _drawing((100, 100, 200, 150), fill=grey),
        _drawing((100, -400, 200, -350), fill=grey),
    ]
    assert _find_invisible_white_fill_artifacts(drawings, PAGE) == set()


def test_degenerate_and_fill_less_rects_ignored():
    drawings = [
        {"rect": fitz.Rect(0, 0, 0, 0), "fill": (1, 1, 1), "color": None},  # zero area
        {"rect": fitz.Rect(100, 100, 200, 150), "fill": None, "color": None},  # no fill
    ]
    assert _find_invisible_white_fill_artifacts(drawings, PAGE) == set()
