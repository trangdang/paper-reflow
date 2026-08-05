# paper-reflow

Convert a two-column technical PDF into a single-column, phone-friendly reflowed PDF. A vibe-coded
exercise.

## Objective

Two-column technical papers are painful to read on a phone: you have to zoom into the left column,
scroll down, then scroll all the way back up to follow the right column. `paper-reflow` rewrites the
paper into one narrow column laid out in true reading order, while keeping the output as vector
content (selectable text, crisp figures).

## Usage

This project uses [`uv`](https://github.com/astral-sh/uv).

```bash
# Install dependencies
uv sync

# Reflow a PDF
uv run reflow.py <path1/input.pdf> <path2/output.pdf>
```

Alongside `<path2/output.pdf>`, the tool always writes debug artifacts:

- `<path2/output>.debug-bboxes.pdf` — source pages with detected element boxes, kind/column
  labels, and the gutter region overlaid.
- `<path2/output>.warnings.log` — written only if layout padding produced residual box overlaps.

Sanity-check that no text was dropped or duplicated:

```bash
uv run scripts/check_text_fidelity.py <input.pdf> <output.pdf>
```

## Web UI

A static, client-side page runs the same pipeline entirely in the browser via
[Pyodide](https://pyodide.org/) (PyMuPDF is loaded as a bundled Pyodide package) — no server, no
upload. Build the source bundle and serve `web/`:

```bash
# Bundle the reflow core into web/app.zip (rebuild after changing lib/ or workflow/)
./web/build.sh

# Serve over http (file:// won't work — Pyodide needs an origin)
uv run python -m http.server -d web 8000
# then open http://localhost:8000
```

Pick a PDF and download the reflowed result. The first load fetches the Pyodide runtime and PyMuPDF
(~25 MB, cached afterwards). `web/app.zip` is a generated artifact (git-ignored); regenerate it with
`web/build.sh` whenever the Python core changes.

## Approach

Built on [PyMuPDF](https://pymupdf.readthedocs.io/) (`fitz`). The pipeline is a straight-line
sequence — reading `reflow.py` top to bottom gives the whole flow:

1. **Detect the column layout.** Find the vertical whitespace gutter between the two columns from an
   x-axis occupancy histogram. Gutter width is a document-level constant, so it's detected per-page
   and then pinned to a document-wide consensus.

2. **Classify blocks.** Assign each content block to the left column, right column, or a
   page-spanning region, and label its kind (paragraph / heading / figure / table / equation).
   Figures and tables are matched to their captions by proximity.

3. **Compute reading order.** Flatten each page into a single sequence — left column top-to-bottom,
   then right column, with spanning elements interleaved at their vertical position — producing a
   document-wide list of elements in the order a human would read them.

4. **Paginate and render.** Greedily pack the ordered elements onto fixed-height phone-sized pages
   (never splitting an element across a break), then re-render each element by clipping it out of
   the original PDF, preserving vectors and text.

Heuristic thresholds (gutter width, column-membership fraction, caption search distance, padding 
steps, output page size) live in `lib/config.py`.


## Testing
A sample of papers used heavily for testing:
- https://arxiv.org/abs/1812.01537
