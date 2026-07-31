#!/usr/bin/env python3
"""Benchmark end-to-end reflow latency: runs reflow.run() over a set of PDFs
and reports wall-clock time per file, so regressions in pipeline speed are
easy to spot as the detection/merging passes evolve.

Defaults to every PDF in sample-papers/. Output artifacts are written to a
temp directory and discarded; only timing is reported.
"""

import argparse
import contextlib
import glob
import io
import pathlib
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import reflow

DEFAULT_GLOB = "sample-papers/*.pdf"


def bench_file(path: str, repeats: int, tmpdir: str) -> list[float]:
    out_path = str(pathlib.Path(tmpdir) / (pathlib.Path(path).stem + ".pdf"))
    times = []
    for _ in range(repeats):
        start = time.perf_counter()
        with contextlib.redirect_stdout(io.StringIO()):
            reflow.run(path, out_path)
        times.append(time.perf_counter() - start)
    return times


def main():
    parser = argparse.ArgumentParser(description="Benchmark end-to-end reflow latency.")
    parser.add_argument(
        "inputs",
        nargs="*",
        help=f"PDF file(s) to benchmark (default: {DEFAULT_GLOB})",
    )
    parser.add_argument(
        "--repeats", type=int, default=3, help="runs per file to measure (default: 3)"
    )
    args = parser.parse_args()

    inputs = args.inputs or sorted(glob.glob(DEFAULT_GLOB))
    if not inputs:
        print(f"no PDFs found (looked for {DEFAULT_GLOB})", file=sys.stderr)
        return 1

    print(f"{'file':<30} {'min':>8} {'median':>8} {'mean':>8}  (n={args.repeats})")
    with tempfile.TemporaryDirectory() as tmpdir:
        for path in inputs:
            name = pathlib.Path(path).name
            try:
                times = bench_file(path, args.repeats, tmpdir)
            except Exception as exc:
                print(f"{name:<30} FAILED: {exc}")
                continue
            print(
                f"{name:<30} {min(times):>7.2f}s {statistics.median(times):>7.2f}s "
                f"{statistics.mean(times):>7.2f}s"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
