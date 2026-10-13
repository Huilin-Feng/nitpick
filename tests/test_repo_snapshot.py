import pytest

from nitpick.repo_snapshot import RepoSnapshot, ToolError


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "services.py").write_text("def total(items):\n    return sum(items)\n")
    (root / "app" / "api.py").write_text("from app.services import total\n\nprint(total([1, 2]))\n")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("total = secret\n")
    (tmp_path / "outside.txt").write_text("do not read me\n")
    return root


def test_read_file_returns_numbered_lines(repo):
    out = RepoSnapshot(repo).read_file("app/services.py")
    assert out == "1: def total(items):\n2:     return sum(items)"


def test_read_file_line_range(repo):
    out = RepoSnapshot(repo).read_file("app/api.py", start_line=3, end_line=3)
    assert out == "3: print(total([1, 2]))"


def test_read_file_caps_lines_per_call(repo):
    (repo / "big.py").write_text("\n".join(f"x{i} = {i}" for i in range(50)))
    out = RepoSnapshot(repo, max_read_lines=10).read_file("big.py")
    assert out.splitlines()[-2].startswith("10: ")
    assert "40 more lines" in out


def test_read_file_start_past_end(repo):
    with pytest.raises(ToolError, match="only 2 lines"):
        RepoSnapshot(repo).read_file("app/services.py", start_line=99)


def test_missing_file(repo):
    with pytest.raises(ToolError, match="not found"):
        RepoSnapshot(repo).read_file("app/nope.py")


@pytest.mark.parametrize("bad", ["../outside.txt", "app/../../outside.txt", "/etc/passwd", ""])
def test_rejects_paths_outside_repo(repo, bad):
    with pytest.raises(ToolError):
        RepoSnapshot(repo).read_file(bad)


def test_rejects_symlink_escape(repo, tmp_path):
    (repo / "link.txt").symlink_to(tmp_path / "outside.txt")
    with pytest.raises(ToolError, match="outside"):
        RepoSnapshot(repo).read_file("link.txt")


def test_rejects_binary_and_large_files(repo):
    (repo / "logo.png").write_bytes(b"\x89PNG\x00\x00")
    (repo / "huge.py").write_text("x" * 500)
    snap = RepoSnapshot(repo, max_file_bytes=100)
    with pytest.raises(ToolError, match="Binary"):
        snap.read_file("logo.png")
    with pytest.raises(ToolError, match="too large"):
        snap.read_file("huge.py")


def test_search_finds_all_usages_and_skips_hidden_dirs(repo):
    out = RepoSnapshot(repo).search_code("total")
    assert "app/api.py:1: from app.services import total" in out
    assert "app/services.py:1: def total(items):" in out
    assert ".git" not in out


def test_search_does_not_follow_symlinks(repo, tmp_path):
    (repo / "link.txt").symlink_to(tmp_path / "outside.txt")
    assert "No matches" in RepoSnapshot(repo).search_code("do not read")


def test_search_truncates(repo):
    (repo / "many.py").write_text("\n".join("total()" for _ in range(30)))
    out = RepoSnapshot(repo, max_search_results=5).search_code("total()")
    assert out.count("many.py:") == 5
    assert "more matches not shown" in out


@pytest.mark.parametrize("bad", ["", " ", "a", "x" * 201])
def test_search_rejects_bad_queries(repo, bad):
    with pytest.raises(ToolError):
        RepoSnapshot(repo).search_code(bad)


def test_no_matches(repo):
    assert RepoSnapshot(repo).search_code("nonexistent_name") == "No matches for 'nonexistent_name'"
