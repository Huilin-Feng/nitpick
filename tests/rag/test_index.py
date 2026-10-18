import subprocess

import pytest

from nitpick.rag.embedder import HashEmbedder
from nitpick.rag.index import build_index, checkout_at, collection_name, list_source_files
from nitpick.rag.store import InMemoryStore


def test_list_source_files_skips_non_code_and_ignored_dirs(sample_repo):
    assert list_source_files(sample_repo) == ["checkout.py", "orders.py", "ui/banner.py"]


def test_build_index_then_reuse(sample_repo):
    store, emb = InMemoryStore(), HashEmbedder()
    stats = build_index(sample_repo, store, emb)
    assert stats.files == 3 and stats.chunks == store.count() > 0
    assert stats.embedding_tokens > 0 and not stats.reused
    again = build_index(sample_repo, store, emb)
    assert again.reused and again.embedding_tokens == 0


def test_collection_name_is_chroma_safe():
    name = collection_name("owner/my repo!", "abcdef1234567890")
    assert name == "owner-my-repo-abcdef123456"
    assert collection_name("///", "abc123").startswith("repo-")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_checkout_at_indexes_the_old_commit(tmp_path):
    try:
        _git(tmp_path, "init", "-q")
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "m.py").write_text("def old_name():\n    pass\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "base")
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path, capture_output=True,
                          text=True).stdout.strip()
    (tmp_path / "m.py").write_text("def new_name():\n    pass\n")
    _git(tmp_path, "commit", "-qam", "head")

    with checkout_at(tmp_path, base) as wt:
        store = InMemoryStore()
        build_index(wt, store, HashEmbedder())
        assert store.search_text("old_name") and not store.search_text("new_name")
        path = wt
    assert not path.exists()  # worktree cleaned up


def test_shallow_clone_at_fetches_one_commit(tmp_path):
    from nitpick.rag.index import shallow_clone_at
    src = tmp_path / "src"
    src.mkdir()
    try:
        _git(src, "init", "-q")
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    _git(src, "config", "user.email", "t@t")
    _git(src, "config", "user.name", "t")
    _git(src, "config", "uploadpack.allowReachableSHA1InWant", "true")
    (src / "m.py").write_text("def first():\n    pass\n")
    _git(src, "add", ".")
    _git(src, "commit", "-qm", "one")
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, capture_output=True, text=True).stdout.strip()
    (src / "m.py").write_text("def second():\n    pass\n")
    _git(src, "commit", "-qam", "two")
    with shallow_clone_at(f"file://{src}", sha) as d:
        assert "first" in (d / "m.py").read_text()
        clone = d
    assert not clone.exists()


def test_untracked_dir_inside_git_repo_falls_back_to_walk(tmp_path):
    try:
        _git(tmp_path, "init", "-q")
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    fixture = tmp_path / "fixtures" / "case1" / "after" / "app"
    fixture.mkdir(parents=True)
    (fixture / "api.py").write_text("def invite():\n    pass\n")
    assert list_source_files(tmp_path / "fixtures" / "case1" / "after") == ["app/api.py"]
