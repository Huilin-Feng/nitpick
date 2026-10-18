"""Build the vector index for a repository at a specific commit."""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .chunker import chunk_file
from .embedder import Embedder
from .models import Chunk
from .store import VectorStore

DEFAULT_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".java", ".rb", ".rs", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".php", ".kt", ".swift", ".scala", ".sql", ".sh",
}
IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
                ".mypy_cache", ".pytest_cache", "vendor", "third_party"}
MAX_FILE_BYTES = 200_000  # skip huge/generated files


@dataclass
class IndexStats:
    files: int = 0
    chunks: int = 0
    embedding_tokens: int = 0
    reused: bool = False  # True if an existing persisted index was used


def list_source_files(repo_dir: str | Path, extensions: set[str] = DEFAULT_EXTENSIONS) -> list[str]:
    """Repo-relative paths of source files. Uses `git ls-files` when possible (respects .gitignore)."""
    repo_dir = Path(repo_dir)
    paths: list[str]
    try:
        out = subprocess.run(["git", "ls-files"], cwd=repo_dir, capture_output=True,
                             text=True, check=True).stdout
        paths = [p for p in out.splitlines() if p]
    except (subprocess.CalledProcessError, FileNotFoundError):
        paths = []
    if not paths:
        # Not a git repo, or a directory whose files are untracked
        # (e.g. a new evaluation fixture): walk the file system instead.
        for root, dirs, files in os.walk(repo_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for f in files:
                paths.append(Path(root, f).relative_to(repo_dir).as_posix())
    result = []
    for p in paths:
        if Path(p).suffix.lower() not in extensions:
            continue
        if any(part in IGNORED_DIRS for part in Path(p).parts):
            continue
        full = repo_dir / p
        if not full.is_file() or full.stat().st_size > MAX_FILE_BYTES:
            continue
        result.append(p)
    return sorted(result)


def collect_chunks(repo_dir: str | Path, extensions: set[str] = DEFAULT_EXTENSIONS) -> tuple[list[Chunk], int]:
    repo_dir = Path(repo_dir)
    files = list_source_files(repo_dir, extensions)
    chunks: list[Chunk] = []
    for rel in files:
        try:
            source = (repo_dir / rel).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary or non-utf8 file
        chunks.extend(chunk_file(rel, source))
    return chunks, len(files)


def build_index(repo_dir: str | Path, store: VectorStore, embedder: Embedder,
                extensions: set[str] = DEFAULT_EXTENSIONS) -> IndexStats:
    """Chunk every source file, embed, and add to the store.

    If the store already has data (persisted from an earlier run), reuse it.
    """
    if store.count() > 0:
        return IndexStats(chunks=store.count(), reused=True)
    chunks, n_files = collect_chunks(repo_dir, extensions)
    before = embedder.tokens_used
    if chunks:
        vectors = embedder.embed([c.embedding_text() for c in chunks])
        store.add(chunks, vectors)
    return IndexStats(files=n_files, chunks=len(chunks),
                      embedding_tokens=embedder.tokens_used - before)


def collection_name(repo_slug: str, commit_sha: str) -> str:
    """Chroma collection names: 3-63 chars, alnum plus . _ -"""
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", repo_slug).strip("-._") or "repo"
    return f"{slug[:48]}-{commit_sha[:12]}"


@contextlib.contextmanager
def checkout_at(repo_dir: str | Path, commit_sha: str) -> Iterator[Path]:
    """Temporarily check out `commit_sha` in a separate git worktree.

    Used by the evaluation: the index must reflect the repo *before* the PR,
    otherwise retrieval could surface the already-fixed code.
    """
    tmp = Path(tempfile.mkdtemp(prefix="nitpick-wt-"))
    target = tmp / "wt"
    subprocess.run(["git", "worktree", "add", "--detach", str(target), commit_sha],
                   cwd=repo_dir, check=True, capture_output=True)
    try:
        yield target
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(target)],
                       cwd=repo_dir, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def shallow_clone_at(clone_url: str, commit_sha: str) -> Iterator[Path]:
    """Fetch a single commit into a temp dir (for the GitHub App, which has no local clone).

    For private repos pass an authenticated URL, e.g.
    https://x-access-token:<installation_token>@github.com/owner/repo.git
    """
    tmp = Path(tempfile.mkdtemp(prefix="nitpick-clone-"))
    try:
        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=tmp, check=True, capture_output=True)
        git("init", "-q")
        git("remote", "add", "origin", clone_url)
        git("fetch", "-q", "--depth", "1", "origin", commit_sha)
        git("checkout", "-q", "FETCH_HEAD")
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
