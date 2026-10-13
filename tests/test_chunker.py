from nitpick.chunker import (
    build_chunks, estimate_tokens, file_priority, is_ignored, render_file, split_file,
)
from nitpick.diff_parser import parse_diff
from nitpick.models import DiffLine, FileDiff, Hunk
from tests.sample_diffs import MULTI


def _big_file(path, hunks, lines_per_hunk):
    f = FileDiff(path=path)
    n = 1
    for _ in range(hunks):
        hunk = Hunk(header=f"@@ -{n},{lines_per_hunk} +{n},{lines_per_hunk} @@")
        for _ in range(lines_per_hunk):
            hunk.lines.append(DiffLine("+", "x = 'some fairly long line of code here'", None, n))
            n += 1
        f.hunks.append(hunk)
    return f


def _lines_in(chunks):
    """All new-file line numbers that made it into the chunks."""
    found = set()
    for c in chunks:
        for row in c.text.splitlines():
            head = row[:5].strip()
            if head.isdigit():
                found.add(int(head))
    return found


def test_render_includes_line_numbers():
    f = parse_diff(MULTI)[0]
    text = render_file(f)
    assert "### File: app/utils.py (modified)" in text
    assert '    2 + def load(path, mode="r"):' in text


def test_skips_binary_and_deleted_files():
    chunks, skipped = build_chunks(parse_diff(MULTI), max_tokens=10_000)
    reasons = {s.path: s.reason for s in skipped}
    assert reasons == {"old.py": "file deleted", "logo.png": "binary file"}
    reviewed = [f.path for c in chunks for f in c.files]
    assert reviewed == ["app/utils.py", "app/new.py", "b.py"]


def test_small_files_share_one_chunk():
    chunks, _ = build_chunks(parse_diff(MULTI), max_tokens=10_000)
    assert len(chunks) == 1


def test_splits_into_multiple_chunks_when_over_budget():
    files = [_big_file(f"f{i}.py", hunks=1, lines_per_hunk=10) for i in range(3)]
    one_file_tokens = estimate_tokens(render_file(files[0]))
    chunks, skipped = build_chunks(files, max_tokens=one_file_tokens + 5)
    assert len(chunks) == 3
    assert skipped == []


def test_large_file_is_split_by_hunk_and_fully_reviewed():
    big = _big_file("big.py", hunks=5, lines_per_hunk=20)
    one_hunk = estimate_tokens(render_file(_big_file("big.py", 1, 20)))
    chunks, skipped = build_chunks([big], max_tokens=one_hunk + 30, max_chunks=10)
    assert len(chunks) == 5
    assert skipped == []
    assert _lines_in(chunks) == set(range(1, 101))
    assert all(c.estimated_tokens <= one_hunk + 30 for c in chunks)
    assert "big.py (modified, continued)" in chunks[1].text


def test_single_huge_hunk_is_split_by_lines():
    big = _big_file("huge.py", hunks=1, lines_per_hunk=200)
    chunks, skipped = build_chunks([big], max_tokens=300, max_chunks=100)
    assert skipped == []
    assert _lines_in(chunks) == set(range(1, 201))
    assert all(c.estimated_tokens <= 300 for c in chunks)
    assert "(part 2)" in chunks[1].text


def test_review_budget_drops_lowest_priority_files_first():
    files = [
        _big_file("README.md", hunks=1, lines_per_hunk=10),
        _big_file("tests/test_app.py", hunks=1, lines_per_hunk=10),
        _big_file("app/core.py", hunks=1, lines_per_hunk=10),
    ]
    one = estimate_tokens(render_file(files[0]))
    chunks, skipped = build_chunks(files, max_tokens=one + 5, max_chunks=2)
    assert [c.files[0].path for c in chunks] == ["app/core.py", "tests/test_app.py"]
    assert {s.path: s.reason for s in skipped} == {"README.md": "not reviewed: review budget reached"}


def test_budget_can_leave_a_file_partially_reviewed():
    big = _big_file("big.py", hunks=4, lines_per_hunk=20)
    one_hunk = estimate_tokens(render_file(_big_file("big.py", 1, 20)))
    chunks, skipped = build_chunks([big], max_tokens=one_hunk + 30, max_chunks=2)
    assert len(chunks) == 2
    assert skipped[0].reason == "partially reviewed: review budget reached"


def test_ignored_files_are_skipped():
    files = [
        _big_file("package-lock.json", 1, 3),
        _big_file("poetry.lock", 1, 3),
        _big_file("static/app.min.js", 1, 3),
        _big_file("web/dist/bundle.js", 1, 3),
        _big_file("app/main.py", 1, 3),
    ]
    chunks, skipped = build_chunks(files, max_tokens=10_000)
    assert [f.path for c in chunks for f in c.files] == ["app/main.py"]
    assert len(skipped) == 4
    assert all(s.reason == "ignored (generated or lock file)" for s in skipped)


def test_is_ignored_directory_pattern_matches_whole_segment_only():
    assert is_ignored("dist/a.js", ["dist/"])
    assert is_ignored("web/dist/a.js", ["dist/"])
    assert not is_ignored("distance/a.py", ["dist/"])


def test_file_priority():
    assert file_priority("app/core.py") == 0
    assert file_priority("tests/test_core.py") == 1
    assert file_priority("app/core_test.py") == 1
    assert file_priority("README.md") == 2
    assert file_priority("pyproject.toml") == 2


def test_split_file_keeps_small_file_whole():
    f = parse_diff(MULTI)[0]
    [piece] = split_file(f, max_tokens=10_000)
    assert piece.text == render_file(f)
