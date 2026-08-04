"""Pure reflow core, shared by every adapter (CLI in reflow.py, browser in
web_adapter.py). Takes an open source Document and returns the final
vector-preserving Document plus the intermediate detection results, with no
filesystem access and no printing -- callers own all I/O.

The debug overlay and fidelity-exclusion artifacts are dev/CLI-only and are
deliberately NOT built here; the CLI adapter derives them from the returned
page_layouts/sequence."""

import statistics
from dataclasses import dataclass

import fitz

from lib.elements import Element, PageLayout
from workflow.bbox_padding import pad_and_snap_bboxes
from workflow.content_bands import detect_content_bands
from workflow.layout import build_page_layout
from workflow.reading_order import build_reading_order, insert_page_breaks
from workflow.render_final import render_final


@dataclass
class ReflowResult:
    """Output of reflow_document. `final_doc` is the reflowed PDF (open, caller
    owns closing it); `page_layouts` and `sequence` are the intermediate
    detection results adapters need for debug/fidelity output; `warnings` holds
    residual bbox-overlap messages from padding/snapping."""

    final_doc: fitz.Document
    page_layouts: list[PageLayout]
    sequence: list[Element]
    warnings: list[str]


def reflow_document(src_doc: fitz.Document) -> ReflowResult:
    # Each page's get_text("dict") is parsed once here and threaded through
    # every pass below (probe layout, content-band detection, final layout)
    # instead of each pass re-parsing it independently.
    text_dicts = [src_doc[i].get_text("dict") for i in range(len(src_doc))]

    # Gutter width is a property of the document's print template, so it
    # shouldn't vary page to page -- but per-page detection is noisy (see
    # build_page_layout). Detect once per page, then re-detect using the
    # document-wide consensus width so every page's column split is
    # consistent.
    probe_layouts = [
        build_page_layout(src_doc[i], i, text_dict=text_dicts[i]) for i in range(len(src_doc))
    ]
    gutter_widths = [
        round(pl.gutter_x[1] - pl.gutter_x[0], 3) for pl in probe_layouts if pl.gutter_x
    ]
    gutter_width = statistics.mode(gutter_widths) if gutter_widths else None

    # Running header/footer strips are a fixed-height document constant (see
    # detect_content_bands). Their text is dropped from the output and
    # figures/graphics are kept from bleeding into them. The band is measured
    # excluding the first page (its masthead differs) but applied to every
    # page, so a first-page running head matching the rest is still caught.
    content_band = detect_content_bands(src_doc, text_dicts=text_dicts)
    page_layouts = [
        build_page_layout(
            src_doc[i],
            i,
            gutter_width_override=gutter_width,
            content_band=content_band,
            text_dict=text_dicts[i],
        )
        for i in range(len(src_doc))
    ]

    warnings: list[str] = []
    for layout in page_layouts:
        warnings.extend(pad_and_snap_bboxes(layout, src_doc[layout.page_no].rect))

    sequence = insert_page_breaks(build_reading_order(page_layouts))
    final_doc = render_final(src_doc, sequence)

    return ReflowResult(
        final_doc=final_doc,
        page_layouts=page_layouts,
        sequence=sequence,
        warnings=warnings,
    )
