#!/usr/bin/env python3
"""CLI adapter: convert a two-column technical PDF into a single-column,
phone-friendly reflow. The reflow logic lives in workflow.pipeline; this module
handles path I/O and the dev-only debug/fidelity artifacts. Always writes a
bbox-overlay debug PDF alongside the requested output."""

import argparse
import json
import pathlib
import sys

import fitz

from lib.elements import Kind
from workflow.pipeline import reflow_document
from workflow.render_overlay import render_overlay


def run(input_path: str, output_path: str) -> None:
    src_doc = fitz.open(input_path)
    result = reflow_document(src_doc)

    out_path = pathlib.Path(output_path)
    overlay_path = out_path.with_suffix(".debug-bboxes.pdf")
    warnings_path = out_path.with_suffix(".warnings.log")
    fidelity_path = out_path.with_suffix(".fidelity-exclusions.json")

    result.final_doc.save(output_path, garbage=4, deflate=True)
    result.final_doc.close()

    overlay_doc = render_overlay(src_doc, result.page_layouts)
    overlay_doc.save(str(overlay_path))
    overlay_doc.close()

    # Word-fidelity nuisance diffs: text that's *supposed* to differ between
    # source and output. Recorded here (where the decisions are actually
    # made) so check_text_fidelity.py can exclude them instead of flagging
    # them as dropped/extra content.
    fidelity_exclusions = {
        # Present in the source but deliberately not carried into the
        # output (e.g. a rotated arXiv identifier stamp in the margin).
        "excluded_source_text": [
            text for layout in result.page_layouts for text in layout.excluded_texts
        ],
        # Present in the output but not in the source (synthetic "Page N"
        # section separators inserted at each source-page boundary).
        "inserted_output_text": [el.text for el in result.sequence if el.kind == Kind.PAGE_BREAK],
    }
    fidelity_path.write_text(json.dumps(fidelity_exclusions, indent=2) + "\n")

    if result.warnings:
        warnings_path.write_text("\n".join(result.warnings) + "\n")
        print(
            f"[paper-reflow] {len(result.warnings)} warning(s) written to {warnings_path}",
            file=sys.stderr,
        )

    print(f"[paper-reflow] wrote {output_path}")
    print(f"[paper-reflow] wrote {overlay_path}")
    print(f"[paper-reflow] wrote {fidelity_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Reflow a two-column technical PDF for phone screens."
    )
    parser.add_argument("input", help="path to source two-column PDF")
    parser.add_argument("output", help="path to write the final reflowed PDF")
    args = parser.parse_args()
    run(args.input, args.output)


if __name__ == "__main__":
    main()
