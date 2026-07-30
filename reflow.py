#!/usr/bin/env python3
"""CLI: convert a two-column academic PDF into a single-column, phone-friendly
reflow. Always writes a bbox-overlay debug PDF alongside the requested
output."""

import argparse
import json
import pathlib
import statistics
import sys

import fitz

from lib.elements import Kind
from workflow.layout import build_page_layout, pad_and_snap_bboxes
from workflow.reading_order import build_reading_order, insert_page_breaks
from workflow.render_final import render_final
from workflow.render_overlay import render_overlay


def run(input_path: str, output_path: str) -> None:
    src_doc = fitz.open(input_path)

    # Gutter width is a property of the document's print template, so it
    # shouldn't vary page to page -- but per-page detection is noisy (see
    # build_page_layout). Detect once per page, then re-detect using the
    # document-wide consensus width so every page's column split is
    # consistent.
    probe_layouts = [build_page_layout(src_doc[i], i) for i in range(len(src_doc))]
    gutter_widths = [
        round(pl.gutter_x[1] - pl.gutter_x[0], 3) for pl in probe_layouts if pl.gutter_x
    ]
    gutter_width = statistics.mode(gutter_widths) if gutter_widths else None

    page_layouts = [
        build_page_layout(src_doc[i], i, gutter_width_override=gutter_width)
        for i in range(len(src_doc))
    ]

    warnings: list[str] = []
    for layout in page_layouts:
        warnings.extend(pad_and_snap_bboxes(layout, src_doc[layout.page_no].rect))

    sequence = insert_page_breaks(build_reading_order(page_layouts))

    out_path = pathlib.Path(output_path)
    overlay_path = out_path.with_suffix(".debug-bboxes.pdf")
    warnings_path = out_path.with_suffix(".warnings.log")
    fidelity_path = out_path.with_suffix(".fidelity-exclusions.json")

    render_overlay(src_doc, page_layouts, str(overlay_path))
    render_final(src_doc, sequence, str(output_path))

    # Word-fidelity nuisance diffs: text that's *supposed* to differ between
    # source and output. Recorded here (where the decisions are actually
    # made) so check_text_fidelity.py can exclude them instead of flagging
    # them as dropped/extra content.
    fidelity_exclusions = {
        # Present in the source but deliberately not carried into the
        # output (e.g. a rotated arXiv identifier stamp in the margin).
        "excluded_source_text": [text for layout in page_layouts for text in layout.excluded_texts],
        # Present in the output but not in the source (synthetic "Page N"
        # section separators inserted at each source-page boundary).
        "inserted_output_text": [el.text for el in sequence if el.kind == Kind.PAGE_BREAK],
    }
    fidelity_path.write_text(json.dumps(fidelity_exclusions, indent=2) + "\n")

    if warnings:
        warnings_path.write_text("\n".join(warnings) + "\n")
        print(
            f"[paper-reflow] {len(warnings)} warning(s) written to {warnings_path}", file=sys.stderr
        )

    print(f"[paper-reflow] wrote {output_path}")
    print(f"[paper-reflow] wrote {overlay_path}")
    print(f"[paper-reflow] wrote {fidelity_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Reflow a two-column academic PDF for phone screens."
    )
    parser.add_argument("input", help="path to source two-column PDF")
    parser.add_argument("output", help="path to write the final reflowed PDF")
    args = parser.parse_args()
    run(args.input, args.output)


if __name__ == "__main__":
    main()
