"""Turn evaluation fixtures into diff files.

Each case lives in fixtures/<case_id>/before/ and fixtures/<case_id>/after/.
For every case we create a throwaway git repo, commit "before", replace it
with "after", and save `git diff` to diffs/<case_id>.diff. This produces
exactly the same unified diff format that GitHub returns.

    python -m evaluation.build_diffs
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
FIXTURES = ROOT / "fixtures"
DIFFS = ROOT / "diffs"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout


def copy_tree(src: Path, dst: Path) -> None:
    if src.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)


def build_case(case_dir: Path) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        git(repo, "init", "-q")
        git(repo, "config", "user.email", "eval@example.com")
        git(repo, "config", "user.name", "eval")

        copy_tree(case_dir / "before", repo)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "--allow-empty", "-m", "before")

        # Remove everything except .git, then lay down the "after" version.
        for child in repo.iterdir():
            if child.name != ".git":
                shutil.rmtree(child) if child.is_dir() else child.unlink()
        copy_tree(case_dir / "after", repo)
        git(repo, "add", "-A")
        # -M detects renames, matching what GitHub shows.
        return git(repo, "diff", "--cached", "--no-color", "-M")


def main() -> None:
    DIFFS.mkdir(exist_ok=True)
    for case_dir in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        diff = build_case(case_dir)
        (DIFFS / f"{case_dir.name}.diff").write_text(diff)
        print(f"built {case_dir.name}")


if __name__ == "__main__":
    main()
