"""Turn a ReviewResult into the Markdown that appears on GitHub."""

from nitpick.models import ReviewComment, ReviewResult

SEVERITY_LABEL = {
    "security": "🔒 Security",
    "bug": "🐛 Bug",
    "performance": "⚡ Performance",
    "style": "✏️ Style",
}


def format_comment(comment: ReviewComment) -> str:
    return f"**{SEVERITY_LABEL[comment.severity.value]}**\n\n{comment.message}"


def format_summary(result: ReviewResult) -> str:
    lines = ["## nitpick review", "", result.summary, ""]
    lines.append(f"Inline comments: {len(result.comments)}")

    if result.notes:
        lines += ["", "<details><summary>Notes on unchanged code</summary>", ""]
        lines += [f"- `{n.path}` line {n.line} ({n.severity.value}): {n.message}" for n in result.notes]
        lines += ["", "</details>"]

    if result.skipped_files:
        lines += ["", "<details><summary>Files not fully reviewed</summary>", ""]
        lines += [f"- `{s.path}`: {s.reason}" for s in result.skipped_files]
        lines += ["", "</details>"]

    if result.failed_chunks:
        lines += ["", f"⚠️ {result.failed_chunks} part(s) of this PR could not be reviewed due to an LLM error."]

    return "\n".join(lines)


def format_comments_inline(result: ReviewResult) -> str:
    """Fallback body when inline comments cannot be posted."""
    parts = [format_summary(result), "", "### Comments"]
    for c in result.comments:
        parts.append(f"- `{c.path}` line {c.line}: {format_comment(c)}")
    return "\n".join(parts)
