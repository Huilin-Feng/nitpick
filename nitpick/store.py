"""Remember which commits have been reviewed (SQLite).

Two problems this solves:
  * Duplicate work. GitHub may deliver the same webhook more than once,
    and a PR can be closed and reopened at the same commit. Each
    (repo, PR, head commit) is reviewed at most once.
  * Incremental reviews. When new commits are pushed, we only want to
    review what changed since the last commit we reviewed.

The claim is done with a PRIMARY KEY constraint instead of "check, then
insert". If two deliveries arrive at the same moment, both would pass a
"has this been reviewed?" check; with the constraint, the database
guarantees that only one INSERT succeeds.
"""

import sqlite3
import time
from collections.abc import Callable
from contextlib import closing

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    repo        TEXT    NOT NULL,
    pr          INTEGER NOT NULL,
    head_sha    TEXT    NOT NULL,
    status      TEXT    NOT NULL,  -- in_progress | done | failed
    updated_at  REAL    NOT NULL,
    PRIMARY KEY (repo, pr, head_sha)
)
"""

# A review stuck "in_progress" this long is assumed to have crashed
# (e.g. the server restarted mid-review) and may be claimed again.
DEFAULT_STALE_AFTER = 15 * 60


class ReviewStore:
    def __init__(
        self,
        path: str,
        stale_after: float = DEFAULT_STALE_AFTER,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.path = path
        self.stale_after = stale_after
        self._now = now
        with closing(self._connect()) as conn, conn:
            conn.execute(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # A new connection per operation keeps this safe to use from
        # FastAPI background tasks running in different threads.
        return sqlite3.connect(self.path, timeout=10)

    def try_claim(self, repo: str, pr: int, head_sha: str) -> bool:
        """Return True if the caller should review this commit now."""
        now = self._now()
        with closing(self._connect()) as conn, conn:
            try:
                conn.execute(
                    "INSERT INTO reviews VALUES (?, ?, ?, 'in_progress', ?)",
                    (repo, pr, head_sha, now),
                )
                return True
            except sqlite3.IntegrityError:
                pass

            # Already known. Allow a retry only if the earlier attempt failed
            # or is stuck. A single UPDATE with a WHERE clause is atomic, so
            # two concurrent retries cannot both succeed.
            cur = conn.execute(
                """
                UPDATE reviews SET status = 'in_progress', updated_at = ?
                WHERE repo = ? AND pr = ? AND head_sha = ?
                  AND (status = 'failed' OR (status = 'in_progress' AND updated_at < ?))
                """,
                (now, repo, pr, head_sha, now - self.stale_after),
            )
            return cur.rowcount == 1

    def _set_status(self, repo: str, pr: int, head_sha: str, status: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "UPDATE reviews SET status = ?, updated_at = ? WHERE repo = ? AND pr = ? AND head_sha = ?",
                (status, self._now(), repo, pr, head_sha),
            )

    def mark_done(self, repo: str, pr: int, head_sha: str) -> None:
        self._set_status(repo, pr, head_sha, "done")

    def mark_failed(self, repo: str, pr: int, head_sha: str) -> None:
        self._set_status(repo, pr, head_sha, "failed")

    def status(self, repo: str, pr: int, head_sha: str) -> str | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT status FROM reviews WHERE repo = ? AND pr = ? AND head_sha = ?",
                (repo, pr, head_sha),
            ).fetchone()
        return row[0] if row else None

    def last_reviewed_sha(self, repo: str, pr: int) -> str | None:
        """Most recent commit of this PR whose review completed."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT head_sha FROM reviews
                WHERE repo = ? AND pr = ? AND status = 'done'
                ORDER BY updated_at DESC, rowid DESC LIMIT 1
                """,
                (repo, pr),
            ).fetchone()
        return row[0] if row else None
