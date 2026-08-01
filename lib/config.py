"""Tunable constants for the paper-reflow pipeline."""

# --- Gutter / column detection ---
GUTTER_MIN_WIDTH_PT = 6.0
# blocks wider than this fraction of content width don't vote on the gutter
NARROW_BLOCK_MAX_FRACTION = 0.55
# gap must be empty across this fraction of narrow blocks' vertical extent
GUTTER_COVERAGE_MIN_FRACTION = 0.6

# --- Block classification ---
# block must have this much of its width inside a column to be LEFT/RIGHT
COLUMN_MEMBERSHIP_MIN_FRACTION = 0.95
SPANNING_MIN_WIDTH_FRACTION = 0.60  # width relative to a single column's target width

# --- Element width-completion heuristic ---
# Real technical-paper elements are essentially never an arbitrary fraction of
# the content width: they're either about one column wide, or they span both
# columns. Elements narrower than SINGLE_COLUMN_MIN_WIDTH_FRACTION are
# fragments that must be merged with nearby content until they reach a
# natural size (unless nothing nearby completes them). Elements that land in
# the 40-60% band can't cleanly fit in one column, so they're forced to
# SPANNING rather than left ambiguous.
SINGLE_COLUMN_MIN_WIDTH_FRACTION = 0.40  # fraction of content width
TWO_COLUMN_MIN_WIDTH_FRACTION = 0.60  # fraction of content width
# max gap to search for a merge candidate when completing a fragment
ELEMENT_MERGE_SEARCH_GAP_PT = 60.0
# when the merge candidate is already column-width-complete, only a genuine
# trailing-line continuation (a small multiple of one text line's height)
# should still cross into it -- a wide search radius here would let an
# unrelated fragment several lines away (e.g. a table's label column) jump
# into the next section's paragraph just because it's the nearest thing
# geometrically
UNDERSIZED_COMPLETE_NEIGHBOR_GAP_LINES = 2.0

# --- Ambiguous-width reclassification ---
# Unlike the width-completion heuristic above (which compares against page
# width), this compares against the page's own measured column width: a
# fixed page-width fraction is unreliable because real single-column
# content routinely sits right at ~40-48% of page width for normal
# margins/gutters, which the old fixed 0.40-0.60 page-width band treated as
# "ambiguous" and force-spanned every ordinary single-column paragraph.
# an element must be at least this much wider than one column to become ambiguous
AMBIGUOUS_WIDTH_MARGIN_PT = 15.0
# fraction of the full two-column content span above which width is unambiguously spanning
AMBIGUOUS_SPANNING_FRACTION = 0.85

# --- Header / footer stripping ---
HEADER_FOOTER_BAND_FRACTION = 0.05  # top/bottom 5% of page height
HEADER_FOOTER_MAX_HEIGHT_PT = 20.0
HEADER_FOOTER_MAX_WIDTH_PT = 60.0
# Running head/footer detection. Small page-number stamps aside, running
# heads/footers are wider text (a journal name, author list, or copyright
# line) that the small-stamp test above misses. What actually marks them as
# boilerplate rather than body content is recurrence: the same text (ignoring
# the per-page page number) shows up near the top/bottom of many pages. Any
# text block whose letters-only form recurs on at least RUNNING_HEAD_MIN_PAGES
# pages, within HEADER_FOOTER_ZONE_FRACTION of the top/bottom edge, defines the
# reserved band and is dropped from the output.
HEADER_FOOTER_ZONE_FRACTION = 0.07  # top/bottom search band for running heads
RUNNING_HEAD_MIN_PAGES = 2

# --- Element grouping ---
FIGURE_CLUSTER_GAP_PT = 4.0  # max gap between drawing rects to merge into one cluster
CAPTION_MAX_DISTANCE_PT = 40.0
# cluster/caption must horizontally overlap (within this tolerance) to be matched
CAPTION_MAX_X_GAP_PT = 10.0
MIN_DRAWING_CLUSTER_AREA_PT2 = 20.0 * 20.0
# max vertical gap allowed between a heading-classified figure label and an
# orphan "Figure N" caption below it, when nothing else sits between them, to
# still count as one figure (see merge_orphan_figure_captions)
ORPHAN_FIGURE_LABEL_MAX_GAP_PT = 250.0
# Drawings that are stroke-free, fully opaque, near-pure-white fills are invisible on a white
# page (e.g. leftover boxed-equation template artifacts) but still register as "figure" content
# to naive area-based clustering -- filter them out before clustering.
WHITE_FILL_MIN_COMPONENT = 0.95  # each RGB component must be at least this to count as "white"

# --- Table rule-line detection ---
# Ruled table borders are drawn as thin stroked lines (zero-width or
# zero-height rects in get_drawings), not filled area, so they're invisible
# to the figure-drawing clustering above. Cluster them separately.
# max gap between rule-line fragments to cluster into one table frame
TABLE_RULE_CLUSTER_GAP_PT = 20.0
TABLE_RULE_MIN_LINES = 6  # min rule fragments required before a cluster counts as a ruled table
# min distinct vertical rule x-positions (column ticks) required
TABLE_RULE_MIN_DISTINCT_VERTICALS = 5
# a horizontal rule must span at least this fraction of the region's width to count
TABLE_RULE_FULL_WIDTH_FRACTION = 0.9
# min distinct full-width horizontal rules (e.g. header/inter-row/bottom) required
TABLE_RULE_MIN_FULL_WIDTH_LINES = 3

# --- Bbox padding / snapping ---
VERTICAL_PAD_PT = 2.5
SNAP_STEP_PT = 1.0
BBOX_OVERLAP_TOLERANCE_PT = 2.0

# --- Output layout ---
TARGET_COLUMN_WIDTH_PT = 380.0
OUTPUT_PAGE_HEIGHT_PT = 780.0
OUTPUT_MARGIN_PT = 50.0
INTER_ELEMENT_GAP_PT = 8.0

# --- Source-page reference markers ---
# Inserted into the reading order at every original-PDF page boundary so the
# reflowed output can be traced back to the source page it came from.
PAGE_BREAK_HEIGHT_PT = 20.0
PAGE_BREAK_FONT_SIZE = 7.0
PAGE_BREAK_TEXT_COLOR = (0.6, 0.6, 0.6)
PAGE_BREAK_LINE_COLOR = (0.85, 0.85, 0.85)
PAGE_BREAK_LINE_WIDTH = 0.5
