"""Tunable constants for the paper-reflow pipeline."""

# --- Gutter / column detection ---
GUTTER_MIN_WIDTH_PT = 6.0
NARROW_BLOCK_MAX_FRACTION = 0.55  # blocks wider than this fraction of content width don't vote on the gutter
GUTTER_COVERAGE_MIN_FRACTION = 0.6  # gap must be empty across this fraction of narrow blocks' vertical extent

# --- Block classification ---
COLUMN_MEMBERSHIP_MIN_FRACTION = 0.95  # block must have this much of its width inside a column to be LEFT/RIGHT
SPANNING_MIN_WIDTH_FRACTION = 0.60  # width relative to a single column's target width

# --- Element width-completion heuristic ---
# Real academic-paper elements are essentially never an arbitrary fraction of
# the page width: they're either about one column wide, or they span both
# columns. Elements narrower than SINGLE_COLUMN_MIN_WIDTH_FRACTION are
# fragments that must be merged with nearby content until they reach a
# natural size (unless nothing nearby completes them). Elements that land in
# the 40-60% band can't cleanly fit in one column, so they're forced to
# SPANNING rather than left ambiguous.
SINGLE_COLUMN_MIN_WIDTH_FRACTION = 0.40  # fraction of page width
TWO_COLUMN_MIN_WIDTH_FRACTION = 0.60  # fraction of page width
ELEMENT_MERGE_SEARCH_GAP_PT = 60.0  # max gap to search for a merge candidate when completing a fragment

# --- Ambiguous-width reclassification ---
# Unlike the width-completion heuristic above (which compares against page
# width), this compares against the page's own measured column width: a
# fixed page-width fraction is unreliable because real single-column
# content routinely sits right at ~40-48% of page width for normal
# margins/gutters, which the old fixed 0.40-0.60 page-width band treated as
# "ambiguous" and force-spanned every ordinary single-column paragraph.
AMBIGUOUS_WIDTH_MARGIN_PT = 15.0  # an element must be at least this much wider than one column to become ambiguous
AMBIGUOUS_SPANNING_FRACTION = 0.85  # fraction of the full two-column content span above which width is unambiguously spanning

# --- Header / footer stripping ---
HEADER_FOOTER_BAND_FRACTION = 0.05  # top/bottom 5% of page height
HEADER_FOOTER_MAX_HEIGHT_PT = 20.0
HEADER_FOOTER_MAX_WIDTH_PT = 60.0

# --- Element grouping ---
FIGURE_CLUSTER_GAP_PT = 4.0  # max gap between drawing rects to merge into one cluster
CAPTION_MAX_DISTANCE_PT = 40.0
CAPTION_MAX_X_GAP_PT = 10.0  # cluster/caption must horizontally overlap (within this tolerance) to be matched
MIN_DRAWING_CLUSTER_AREA_PT2 = 20.0 * 20.0

# --- Table rule-line detection ---
# Ruled table borders are drawn as thin stroked lines (zero-width or
# zero-height rects in get_drawings), not filled area, so they're invisible
# to the figure-drawing clustering above. Cluster them separately.
TABLE_RULE_CLUSTER_GAP_PT = 20.0  # max gap between rule-line fragments to cluster into one table frame
TABLE_RULE_MIN_LINES = 6  # min rule fragments required before a cluster counts as a ruled table
TABLE_RULE_MIN_DISTINCT_VERTICALS = 5  # min distinct vertical rule x-positions (column ticks) required

# --- Bbox padding / snapping ---
VERTICAL_PAD_PT = 2.5
SNAP_STEP_PT = 1.0
BBOX_OVERLAP_TOLERANCE_PT = 2.0

# --- Output layout ---
TARGET_COLUMN_WIDTH_PT = 380.0
OUTPUT_PAGE_HEIGHT_PT = 780.0
OUTPUT_MARGIN_PT = 16.0
INTER_ELEMENT_GAP_PT = 8.0

# --- Rendering ---
DRAFT_DPI = 200
