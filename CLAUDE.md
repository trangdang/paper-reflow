# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this
repository. Also see AGENTS.md for repo workflow rules which take precedence on coding process,
leaving this file to focus on architecture.

## What this is

`paper-reflow` converts a two-column technical PDF into a single-column, phone-friendly reflowed PDF
using PyMuPDF (`fitz`). It detects the two-column gutter, classifies content blocks
(paragraph/heading/figure/table/equation), computes a document-wide reading order, and re-renders
the content as vector-preserving pages sized for a phone screen.

## Commands

This project uses `uv` for dependency management (see `uv.lock`).

```bash
# Install deps
uv sync

# Run the reflow pipeline
uv run reflow.py <input.pdf> <output.pdf>

# Run tests
uv run pytest
uv run pytest tests/test_foo.py::test_name   # single test

# Word-fidelity sanity check: confirms the reflowed PDF has the same
# word multiset as the source (catches glyph-cutting/gutter-clip bugs
# where text gets dropped, duplicated, or truncated)
uv run scripts/check_text_fidelity.py <source.pdf> <output.pdf>

# Lint / format (ruff, 100-char lines; see [tool.ruff] in pyproject.toml)
uv run ruff check .
uv run ruff format .

# E2E benchmark
uv run scripts/bench_e2e.py
```

`tests/` holds fast unit tests over the pure detection helpers — element merging
(`_merge_overlapping_same_kind_elements`, `_complete_undersized_elements`'s cross-gutter guard),
white-fill artifact detection (`_find_invisible_white_fill_artifacts`), the `Bbox` primitives, and
the fidelity-check word accounting. They run in well under a second with hand-built `Element`/`Bbox`
inputs (no PDF needed); a root `conftest.py` puts the project root on `sys.path` so `lib`/`workflow`/
`scripts` import without an installed package. Passes that need a real `fitz.Page` (gutter detection,
drawing/rule clustering, full `build_page_layout`) are not unit-tested — `sample-papers/micro_lie.pdf`
remains the reference document for manual/exploratory end-to-end verification of the pipeline.

`reflow.py` always writes debug artifacts next to the requested output, useful when debugging
layout/ordering issues:
- `<output>.pdf` — final vector-preserving reflow
- `<output>.debug-bboxes.pdf` — original pages with detected element bboxes, kind/column labels, and
  the gutter region overlaid
- `<output>.warnings.log` — written only if padding/snapping produced residual bbox overlaps

## Pipeline architecture

The pipeline is a straight-line sequence of per-page detection passes feeding into document-wide
ordering and pagination. Reading `reflow.py` top to bottom gives the full flow; the modules below do
the actual work.

1. **`workflow/layout.py` — per-page detection orchestrator** (the bulk of the logic, now split
   across a handful of `workflow/` modules that `build_page_layout` calls in sequence)
   - `workflow/content_bands.py` (`get_text_blocks`, `detect_content_bands`): strips header/footer
     running text and returns the per-page text blocks and vertical content band that everything
     else operates on.
   - `workflow/gutter.py` (`detect_gutter`, `pin_gutter_width`, `classify_block`): `detect_gutter`
     finds the vertical whitespace gap between columns by building an x-axis occupancy histogram
     from "narrow" blocks (blocks that don't straddle the page center). Gutter detection is
     intentionally redone *twice* in `reflow.py`: once per-page to get a probe estimate, then a
     document-wide consensus width (`statistics.mode`) is computed and every page is re-detected
     pinned to that width via `pin_gutter_width`, because single-page detection is noisy but gutter
     width is a document-level constant. `classify_block` assigns each block to `Column.LEFT` /
     `RIGHT` / `SPANNING` (or `SINGLE` on single-column pages) based on fractional overlap with the
     detected column extents.
   - `workflow/figures.py`: figures/tables/captions are matched by proximity, not containment:
     drawing-rect clusters (`_cluster_drawings`) and ruled-table-line clusters
     (`_table_rule_regions`) are detected separately (filled drawings vs. zero-width/zero-height
     stroked rule lines), then nearby "Figure N"/"Table N" caption text blocks are absorbed into
     them (`_merge_table_rule_regions`, `_merge_orphan_figure_captions`, `_build_figure_graphic_elements`).
   - `workflow/element_merging.py`: the element grouping passes run in a specific, order-dependent
     sequence at the end of `build_page_layout`: table-rule-region merging → adjacent-paragraph
     merging → same-column overlap merging → undersized-element completion → overlap merging again
     → ambiguous-width reclassification. Each pass exists to fix a specific failure mode seen in
     real papers (see the docstring above each function) — don't reorder them without understanding
     why a given pass runs before/after its neighbors.
   - `workflow/bbox_padding.py` (`pad_and_snap_bboxes`): expands each element's tight bbox outward
     (`VERTICAL_PAD_PT`) toward its column's real margins, then whitespace-snaps any resulting
     overlaps between neighbors' padded boxes inward in `SNAP_STEP_PT` increments — floored at the
     tight bbox, so padding is only ever given back, never cut into real content.

2. **`workflow/reading_order.py`**: turns each page's classified elements into a flat sequence — left
   column top-to-bottom, then right column top-to-bottom, with `SPANNING` elements interleaved at
   their own vertical position (flushing all fully-above left/right content before each spanning
   element). This is where "left column, then right column" becomes a literal document-wide list of
   `Element`s in final reading order.

3. **`workflow/pagination.py`**: `plan_pagination` greedily packs the ordered element sequence onto
   fixed-height output pages (never splitting one element across a page break), scaling each
   element's height to a constant target column width. This pagination plan is the single source of
   truth for page breaks — `workflow/render_final.py` reuses the exact same `plan_pagination` call to
   produce the output.

   **`workflow/render_overlay.py`**: renders the bbox/label debug PDF (`render_overlay`) directly
   from each page's `PageLayout`, independent of the pagination plan.

4. **`workflow/render_final.py`**: replays the pagination plan using `show_pdf_page(clip=...)`
   instead of rasterization, so the output stays real vector content (selectable text, crisp vector
   figures) rather than an image.

## Key data model (`lib/elements.py`)

- `Bbox` — simple x0/y0/x1/y1 rect with `intersects`/`union` helpers used throughout the
  clustering/merging passes.
- `Element` — has both `bbox` (tight, used for reading-order y-position decisions) and `padded_bbox`
  (whitespace-snapped clip region, set later by `pad_and_snap_bboxes`). Don't conflate the two:
  ordering logic should read `bbox`/`y0`/`y1`; rendering/clipping should read `padded_bbox`.
- `Column` — `LEFT` / `RIGHT` / `SPANNING` (two-column pages) or `SINGLE` (whole-width column on a
  single-column page).
- `PageLayout` — one page's detected column geometry plus its `elements`.

`lib/blocks.py` (`_block_bbox`, `_block_text`, `_content_x_extent`) and `lib/clustering.py` hold
small shared helpers used by multiple `workflow/` modules above.

## Tuning

Nearly every heuristic threshold (gutter width, column-membership fraction, width-completion bands,
caption search distances, padding/snapping steps, output page size) lives in `lib/config.py`, not
inline in the detection code. When a layout-detection bug looks like "off by a threshold" rather than
a logic error, check `lib/config.py` first.
