#!/usr/bin/env python3
"""Standalone sanity check: does the reflowed output contain the same words
as the source, just reordered? A dropped, duplicated, or truncated word is
the direct symptom of a glyph-cutting gutter clip bug.

Some diffs are expected by design rather than bugs -- e.g. a rotated arXiv
identifier stamp is intentionally dropped from the output, and synthetic
"Page N" section separators are intentionally added to it. reflow.py records
these explicit inclusions/exclusions in a `<output>.fidelity-exclusions.json`
file next to the output; this script loads it (if present) and discounts
that text from the comparison instead of flagging it.
"""

import argparse
import json
import pathlib
import re
import sys
from collections import Counter

import fitz

WORD_RE = re.compile(r"\w+", re.UNICODE)


def extract_words(path: str) -> Counter:
    doc = fitz.open(path)
    words = Counter()
    for page in doc:
        text = page.get_text()
        words.update(WORD_RE.findall(text.lower()))
    return words


def words_from_texts(texts: list[str]) -> Counter:
    words = Counter()
    for text in texts:
        words.update(WORD_RE.findall(text.lower()))
    return words


def load_exclusions(path: pathlib.Path) -> dict:
    if not path.is_file():
        return {"excluded_source_text": [], "inserted_output_text": []}
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser(
        description="Diff word multisets between source and reflowed PDF."
    )
    parser.add_argument("source")
    parser.add_argument("output")
    parser.add_argument(
        "--exclusions",
        help="path to the *.fidelity-exclusions.json file written by reflow.py "
        "(default: derived from the output path)",
    )
    args = parser.parse_args()

    exclusions_path = (
        pathlib.Path(args.exclusions)
        if args.exclusions
        else pathlib.Path(args.output).with_suffix(".fidelity-exclusions.json")
    )
    exclusions = load_exclusions(exclusions_path)

    src_words = extract_words(args.source)
    out_words = extract_words(args.output)

    excluded_source_words = words_from_texts(exclusions["excluded_source_text"])
    inserted_output_words = words_from_texts(exclusions["inserted_output_text"])

    src_words -= excluded_source_words
    out_words -= inserted_output_words

    missing = src_words - out_words
    extra = out_words - src_words

    src_total = sum(src_words.values())
    out_total = sum(out_words.values())
    print(f"source words: {src_total}, output words: {out_total}")
    if exclusions["excluded_source_text"] or exclusions["inserted_output_text"]:
        print(
            f"(discounted {sum(excluded_source_words.values())} excluded source word(s) and "
            f"{sum(inserted_output_words.values())} inserted output word(s) per {exclusions_path})"
        )

    if missing:
        print(f"\nMISSING ({sum(missing.values())} occurrences, {len(missing)} distinct words):")
        for w, c in missing.most_common(30):
            print(f"  {w!r}: -{c}")
    if extra:
        print(
            f"\nEXTRA/DUPLICATED ({sum(extra.values())} occurrences, {len(extra)} distinct words):"
        )
        for w, c in extra.most_common(30):
            print(f"  {w!r}: +{c}")

    if not missing and not extra:
        print("OK: word multisets match exactly.")
        return 0

    print("\nFAIL: word multisets differ.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
