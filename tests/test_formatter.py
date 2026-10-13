from nitpick.formatter import format_comment, format_summary
from nitpick.models import ReviewComment, ReviewResult, Severity, SkippedFile


def test_summary_lists_skipped_files_and_failures():
    result = ReviewResult(
        summary="Looks fine.",
        skipped_files=[SkippedFile("logo.png", "binary file")],
        failed_chunks=1,
    )
    text = format_summary(result)
    assert "Looks fine." in text
    assert "`logo.png`: binary file" in text
    assert "could not be reviewed" in text


def test_comment_has_severity_label():
    c = ReviewComment("a.py", 1, Severity.SECURITY, "SQL injection risk.")
    assert format_comment(c).startswith("**🔒 Security**")


def test_summary_includes_notes_on_unchanged_code():
    note = ReviewComment("a.py", 9, Severity.BUG, "Caller not updated.")
    text = format_summary(ReviewResult(summary="s", notes=[note]))
    assert "Notes on unchanged code" in text
    assert "`a.py` line 9 (bug): Caller not updated." in text
