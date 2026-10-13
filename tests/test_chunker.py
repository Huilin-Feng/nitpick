from nitpick.chunker import build_chunks, estimate_tokens, render_file
from nitpick.diff_parser import parse_diff
from nitpick.models import DiffLine, FileDiff, Hunk
from tests.sample_diffs import MULTI


def _big_file(path, hunks, lines_per_hunk):
    f = FileDiff(path=path)
    n = 1
    for h in range(hunks):
        hunk = Hunk(header=f"@@ -{n},{lines_per_hunk} +{n},{lines_per_hunk} @@")
        for _ in range(lines_per_hunk):
            hunk.lines.append(DiffLine("+", "x = 'some fairly long line of code here'", None, n))
            n += 1
        f.hunks.append(hunk)
    return f


def test_render_includes_line_numbers():
    [f] = parse_diff(MULTI)[:1]
    text, truncated = render_file(f)
    assert not truncated
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
    one_file_tokens = estimate_tokens(render_file(files[0])[0])
    chunks, skipped = build_chunks(files, max_tokens=one_file_tokens + 5)
    assert len(chunks) == 3
    assert skipped == []
    assert all(c.estimated_tokens <= one_file_tokens + 5 for c in chunks)


def test_oversized_file_is_partially_reviewed():
    big = _big_file("big.py", hunks=5, lines_per_hunk=20)
    first_hunk_tokens = estimate_tokens(render_file(_big_file("big.py", 1, 20))[0])
    chunks, skipped = build_chunks([big], max_tokens=first_hunk_tokens + 10)
    assert len(chunks) == 1
    assert chunks[0].text.count("@@") == 2  # one hunk header = two "@@"
    assert skipped[0].reason == "partially reviewed: too large"


def test_file_too_large_even_for_one_hunk_is_skipped():
    big = _big_file("huge.py", hunks=1, lines_per_hunk=200)
    chunks, skipped = build_chunks([big], max_tokens=50)
    assert chunks == []
    assert skipped[0].reason == "too large to review"
