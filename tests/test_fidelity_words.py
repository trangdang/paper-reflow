"""Coverage for the word-multiset accounting behind the fidelity check (commit
c6c7f71): intentionally excluded source text and intentionally inserted output
text are discounted from the comparison instead of being flagged as
dropped/added content."""

import json

from scripts.check_text_fidelity import load_exclusions, words_from_texts


def test_words_from_texts_lowercases_and_tokenizes():
    counts = words_from_texts(["Hello WORLD", "hello arXiv:1234"])
    assert counts["hello"] == 2
    assert counts["world"] == 1
    assert counts["arxiv"] == 1
    assert counts["1234"] == 1


def test_words_from_texts_empty():
    assert words_from_texts([]) == words_from_texts([""])


def test_load_exclusions_missing_file_returns_empty_defaults(tmp_path):
    missing = tmp_path / "does-not-exist.json"
    assert load_exclusions(missing) == {"excluded_source_text": [], "inserted_output_text": []}


def test_load_exclusions_reads_json(tmp_path):
    path = tmp_path / "out.fidelity-exclusions.json"
    payload = {
        "excluded_source_text": ["arXiv:2401.00001"],
        "inserted_output_text": ["Page 1", "Page 2"],
    }
    path.write_text(json.dumps(payload))
    assert load_exclusions(path) == payload


def test_discounting_cancels_expected_diffs():
    # Mirror the subtraction the checker performs: an intentionally dropped
    # source stamp and an intentionally inserted separator must not show up as
    # missing/extra once discounted.
    src = words_from_texts(["the quick brown fox", "arXiv 2401"])
    out = words_from_texts(["the quick brown fox", "Page 1"])

    excluded_src = words_from_texts(["arXiv 2401"])
    inserted_out = words_from_texts(["Page 1"])

    src -= excluded_src
    out -= inserted_out

    assert (src - out) == words_from_texts([])  # nothing missing
    assert (out - src) == words_from_texts([])  # nothing extra
