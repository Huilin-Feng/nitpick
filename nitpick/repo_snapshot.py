"""Read-only access to a repository snapshot for the review agent.

The agent's tools (read_file, search_code) go through this class. The same
class serves both runtime paths:
  * evaluation: point it at a fixture's `after/` directory
  * webhook server: download the PR's head commit into a temp directory
    first, then point it there

Every path the model asks for is untrusted input, so it is resolved and
checked to stay inside the snapshot root before anything is read.
"""

from pathlib import Path


class ToolError(Exception):
    """A tool call the model got wrong (bad path, missing file, ...).

    The message is sent back to the model as the tool result so it can
    correct itself; it is not a server error.
    """


class RepoSnapshot:
    def __init__(
        self,
        root: str | Path,
        max_file_bytes: int = 200_000,
        max_read_lines: int = 200,
        max_search_results: int = 20,
    ) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f"Snapshot root is not a directory: {self.root}")
        self.max_file_bytes = max_file_bytes
        self.max_read_lines = max_read_lines
        self.max_search_results = max_search_results

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        """Return lines start_line..end_line (1-based, inclusive), numbered."""
        target = self._resolve(path)
        if not target.is_file():
            raise ToolError(f"File not found: {path}")
        lines = self._read_text(target, path).splitlines()

        start = max(1, start_line)
        end = len(lines) if end_line is None else min(end_line, len(lines))
        if start > len(lines):
            raise ToolError(f"{path} has only {len(lines)} lines")
        if end < start:
            raise ToolError("end_line must be greater than or equal to start_line")

        # Cap how much one call can return, so the model cannot pull a
        # whole large file into the prompt in a single step.
        end = min(end, start + self.max_read_lines - 1)
        body = "\n".join(f"{n}: {lines[n - 1]}" for n in range(start, end + 1))
        if end < len(lines):
            body += f"\n... ({len(lines) - end} more lines; request a later range to continue)"
        return body

    def search_code(self, query: str) -> str:
        """Plain substring search across the snapshot; returns 'path:line: text'."""
        query = query.strip()
        if len(query) < 2:
            raise ToolError("Query must be at least 2 characters")
        if len(query) > 200:
            raise ToolError("Query must be at most 200 characters")

        hits: list[str] = []
        truncated = False
        for file in sorted(self.root.rglob("*")):
            rel = file.relative_to(self.root)
            # Skip hidden directories such as .git and anything not a regular file.
            if any(part.startswith(".") for part in rel.parts) or not file.is_file():
                continue
            # A symlink could point outside the snapshot; never follow it.
            if file.is_symlink():
                continue
            try:
                text = self._read_text(file, str(rel))
            except ToolError:
                continue  # binary or too large: not useful for code search
            for n, line in enumerate(text.splitlines(), start=1):
                if query in line:
                    if len(hits) == self.max_search_results:
                        truncated = True
                        break
                    hits.append(f"{rel.as_posix()}:{n}: {line.strip()[:200]}")
            if truncated:
                break

        if not hits:
            return f"No matches for {query!r}"
        if truncated:
            hits.append(f"... more matches not shown (limit {self.max_search_results}); use a more specific query")
        return "\n".join(hits)

    def _resolve(self, path: str) -> Path:
        if not path or Path(path).is_absolute():
            raise ToolError("Path must be relative to the repository root")
        # resolve() collapses '..' and follows symlinks, so checking the
        # result catches both '../secret' and a symlink that escapes.
        target = (self.root / path).resolve()
        if not target.is_relative_to(self.root):
            raise ToolError(f"Path is outside the repository: {path}")
        return target

    def _read_text(self, file: Path, shown_path: str) -> str:
        if file.stat().st_size > self.max_file_bytes:
            raise ToolError(f"File too large to read: {shown_path}")
        data = file.read_bytes()
        if b"\x00" in data:
            raise ToolError(f"Binary file: {shown_path}")
        return data.decode("utf-8", errors="replace")
