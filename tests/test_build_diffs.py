"""Regression test: same-size, same-mtime edits must still produce a diff."""

import os
import shutil

import pytest

from evaluation.build_diffs import build_case

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def test_same_size_and_mtime_change_is_not_lost(tmp_path):
    before = tmp_path / "before" / "app"
    after = tmp_path / "after" / "app"
    before.mkdir(parents=True)
    after.mkdir(parents=True)
    (before / "text.py").write_text("def _slugify(v):\n    return v\n")
    (after / "text.py").write_text("def _to_slug(v):\n    return v\n")  # same length
    # copytree keeps mtimes, so make them identical, as with real fixtures.
    for f in (before / "text.py", after / "text.py"):
        os.utime(f, (1_700_000_000, 1_700_000_000))

    diff = build_case(tmp_path)
    assert "-def _slugify(v):" in diff
    assert "+def _to_slug(v):" in diff
