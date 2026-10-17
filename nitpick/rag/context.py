"""Turn retrieval results into a prompt section."""
from __future__ import annotations

from pathlib import Path

from .models import RetrievalResult

_LANG = {".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx",
         ".go": "go", ".java": "java", ".rb": "ruby", ".rs": "rust", ".c": "c", ".h": "c",
         ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp", ".php": "php", ".kt": "kotlin",
         ".swift": "swift", ".scala": "scala", ".sql": "sql", ".sh": "bash"}

SECTION_INTRO = (
    "## Related code from the repository (not part of this PR)\n"
    "These snippets were retrieved because they reference symbols changed in this PR "
    "or look similar to the changed code. Use them only to check cross-file consistency: "
    "callers that need updating, interfaces or signatures that no longer match, shared "
    "constants, duplicated logic. Do NOT leave review comments on these snippets themselves; "
    "if one reveals a problem, comment on the changed line in the PR that causes it."
)


def format_related_code(result: RetrievalResult) -> str:
    """Returns "" when nothing was retrieved, so callers can append unconditionally."""
    if not result.chunks:
        return ""
    parts = [SECTION_INTRO]
    for item in result.chunks:
        c = item.chunk
        label = f"### {c.path}:L{c.start_line}-L{c.end_line}"
        if c.symbol:
            label += f" ({c.symbol})"
        if item.reason.startswith("symbol:"):
            label += f" — references `{item.reason.split(':', 1)[1]}`"
        else:
            label += f" — similar code (score {item.score:.2f})"
        lang = _LANG.get(Path(c.path).suffix.lower(), "")
        parts.append(f"{label}\n```{lang}\n{c.text}\n```")
    return "\n\n".join(parts)
