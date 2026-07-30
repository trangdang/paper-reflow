#!/usr/bin/env python3
"""Standalone sanity check: does the reflowed output contain the same words
as the source, just reordered? A dropped, duplicated, or truncated word is
the direct symptom of a glyph-cutting gutter clip bug."""

import argparse
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


def main():
    parser = argparse.ArgumentParser(
        description="Diff word multisets between source and reflowed PDF."
    )
    parser.add_argument("source")
    parser.add_argument("output")
    args = parser.parse_args()

    src_words = extract_words(args.source)
    out_words = extract_words(args.output)

    missing = src_words - out_words
    extra = out_words - src_words

    src_total = sum(src_words.values())
    out_total = sum(out_words.values())
    print(f"source words: {src_total}, output words: {out_total}")

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
