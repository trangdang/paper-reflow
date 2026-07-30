"""Shared dataclasses used across the detection / ordering / rendering modules."""

from dataclasses import dataclass, field
from enum import Enum


class Column(Enum):
    LEFT = "left"
    RIGHT = "right"
    SPANNING = "spanning"
    SINGLE = "single"  # whole-width column on a single-column page


class Kind(Enum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    FIGURE = "figure"
    TABLE = "table"
    EQUATION = "equation"
    GRAPHIC = "graphic"  # unmatched drawing cluster
    OTHER = "other"
    PAGE_BREAK = "page_break"  # synthetic marker, not detected from the source PDF


@dataclass
class Bbox:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def intersects(self, other: "Bbox", tolerance: float = 0.0) -> bool:
        return not (
            self.x1 <= other.x0 + tolerance
            or other.x1 <= self.x0 + tolerance
            or self.y1 <= other.y0 + tolerance
            or other.y1 <= self.y0 + tolerance
        )

    def union(self, other: "Bbox") -> "Bbox":
        return Bbox(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )


@dataclass
class Element:
    kind: Kind
    page_no: int
    column: Column
    bbox: Bbox  # tight bbox — used for reading-order y-position decisions
    padded_bbox: Bbox = None  # whitespace-snapped clip bbox — set in the padding pass
    text: str = ""
    source_refs: list = field(default_factory=list)

    @property
    def y0(self) -> float:
        return self.bbox.y0

    @property
    def y1(self) -> float:
        return self.bbox.y1


@dataclass
class PageLayout:
    page_no: int
    left_col_x: tuple[float, float] | None
    right_col_x: tuple[float, float] | None
    gutter_x: tuple[float, float] | None
    is_two_column: bool
    elements: list[Element] = field(default_factory=list)
    # Text of blocks intentionally dropped from the reading order (e.g. a
    # rotated arXiv identifier stamp) -- not part of the reflow output, but
    # recorded so word-fidelity checking can exclude them from the source
    # side of the comparison instead of flagging them as dropped content.
    excluded_texts: list[str] = field(default_factory=list)
