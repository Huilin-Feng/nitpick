from nitpick.diff_parser import parse_diff
from tests.sample_diffs import ADDED, BINARY, DELETED, MODIFIED, MULTI, RENAMED, TRICKY_REMOVED_LINE


def test_modified_file_line_numbers():
    [f] = parse_diff(MODIFIED)
    assert f.path == "app/utils.py"
    assert f.status == "modified"
    added = [(l.new_lineno, l.content) for l in f.hunks[0].lines if l.kind == "+"]
    assert added == [(2, 'def load(path, mode="r"):'), (3, "    # read the whole file")]
    removed = [l for l in f.hunks[0].lines if l.kind == "-"]
    assert removed[0].old_lineno == 2 and removed[0].new_lineno is None


def test_commentable_lines_include_added_and_context_only():
    [f] = parse_diff(MODIFIED)
    assert f.commentable_lines() == {1, 2, 3, 4, 5}


def test_added_lines_exclude_context():
    [f] = parse_diff(MODIFIED)
    assert f.added_lines() == {2, 3}


def test_added_file():
    [f] = parse_diff(ADDED)
    assert f.status == "added"
    assert f.path == "app/new.py"
    assert f.commentable_lines() == {1, 2}


def test_deleted_file_keeps_old_path():
    [f] = parse_diff(DELETED)
    assert f.status == "deleted"
    assert f.path == "old.py"
    assert f.commentable_lines() == set()


def test_renamed_file():
    [f] = parse_diff(RENAMED)
    assert f.status == "renamed"
    assert f.old_path == "a.py"
    assert f.path == "b.py"


def test_binary_file():
    [f] = parse_diff(BINARY)
    assert f.is_binary
    assert f.hunks == []


def test_removed_line_that_looks_like_header():
    [f] = parse_diff(TRICKY_REMOVED_LINE)
    lines = f.hunks[0].lines
    assert lines[0].kind == "-"
    assert lines[0].content == "-- old comment"
    assert f.path == "q.sql"


def test_multiple_files():
    files = parse_diff(MULTI)
    assert [f.path for f in files] == ["app/utils.py", "app/new.py", "old.py", "b.py", "logo.png"]


def test_empty_diff():
    assert parse_diff("") == []
