"""Parse a unified diff (as returned by the GitHub API) into FileDiff objects."""

import re

from nitpick.models import DiffLine, FileDiff, Hunk

HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_diff(diff_text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk: Hunk | None = None
    # True while reading the per-file header (before the first "@@").
    # This matters because a removed line like "-- comment" would
    # otherwise look like a "--- a/path" header line.
    in_header = False
    old_no = new_no = 0

    for raw in diff_text.splitlines():
        if raw.startswith("diff --git "):
            current = _start_file(raw)
            files.append(current)
            hunk = None
            in_header = True
            continue

        if current is None:
            continue

        match = HUNK_HEADER.match(raw)
        if match:
            old_no, new_no = int(match.group(1)), int(match.group(2))
            hunk = Hunk(header=raw)
            current.hunks.append(hunk)
            in_header = False
            continue

        if in_header:
            _apply_header_line(current, raw)
            continue

        if hunk is None:
            continue

        if raw.startswith("\\"):
            # "\ No newline at end of file" carries no content.
            continue
        if raw.startswith("+"):
            hunk.lines.append(DiffLine("+", raw[1:], None, new_no))
            new_no += 1
        elif raw.startswith("-"):
            hunk.lines.append(DiffLine("-", raw[1:], old_no, None))
            old_no += 1
        else:
            # Context line. Some tools strip the leading space from
            # empty context lines, so treat "" as context too.
            content = raw[1:] if raw.startswith(" ") else raw
            hunk.lines.append(DiffLine(" ", content, old_no, new_no))
            old_no += 1
            new_no += 1

    return files


def _start_file(line: str) -> FileDiff:
    # "diff --git a/src/app.py b/src/app.py". The exact path is confirmed
    # later by the "+++ b/..." line; this is only a fallback.
    rest = line[len("diff --git "):]
    split_at = rest.rfind(" b/")
    if split_at == -1:
        return FileDiff(path=rest)
    old = rest[:split_at].removeprefix("a/")
    new = rest[split_at + 3:]
    return FileDiff(path=new, old_path=old)


def _apply_header_line(file: FileDiff, line: str) -> None:
    if line.startswith("new file mode"):
        file.status = "added"
    elif line.startswith("deleted file mode"):
        file.status = "deleted"
    elif line.startswith("rename from "):
        file.old_path = line[len("rename from "):]
        file.status = "renamed"
    elif line.startswith("rename to "):
        file.path = line[len("rename to "):]
    elif line.startswith("Binary files") or line.startswith("GIT binary patch"):
        file.is_binary = True
    elif line.startswith("+++ "):
        target = line[4:]
        if target != "/dev/null":
            file.path = target.removeprefix("b/")
    elif line.startswith("--- "):
        source = line[4:]
        if source != "/dev/null":
            file.old_path = source.removeprefix("a/")
