"""Browser adapter: the entry point a future static HTML page will call from
Pyodide. Mirrors reflow.py's CLI adapter but does all I/O in memory -- no
filesystem, no printing, and no dev-only overlay/fidelity artifacts. The
reflow logic itself lives in workflow.pipeline, shared with the CLI."""

import fitz

from workflow.pipeline import reflow_document


def reflow_bytes(pdf_bytes: bytes) -> bytes:
    """Reflow a source PDF given as bytes and return the reflowed PDF as bytes.
    The caller (JS) wraps the result in a Blob for download."""
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    result = reflow_document(src)
    out = result.final_doc.tobytes(garbage=4, deflate=True)
    result.final_doc.close()
    src.close()
    return out
