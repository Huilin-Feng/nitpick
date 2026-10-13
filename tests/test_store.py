import tempfile
import threading
from pathlib import Path

from nitpick.store import ReviewStore


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _store(clock=None, stale_after=900):
    path = Path(tempfile.mkdtemp()) / "test.db"
    return ReviewStore(str(path), stale_after=stale_after, now=clock or Clock())


def test_first_claim_wins_second_is_refused():
    store = _store()
    assert store.try_claim("o/r", 1, "sha1")
    assert not store.try_claim("o/r", 1, "sha1")


def test_different_commits_and_prs_are_independent():
    store = _store()
    assert store.try_claim("o/r", 1, "sha1")
    assert store.try_claim("o/r", 1, "sha2")
    assert store.try_claim("o/r", 2, "sha1")
    assert store.try_claim("o/other", 1, "sha1")


def test_done_review_is_never_reclaimed():
    store = _store()
    store.try_claim("o/r", 1, "sha1")
    store.mark_done("o/r", 1, "sha1")
    assert not store.try_claim("o/r", 1, "sha1")
    assert store.status("o/r", 1, "sha1") == "done"


def test_failed_review_can_be_retried_once():
    store = _store()
    store.try_claim("o/r", 1, "sha1")
    store.mark_failed("o/r", 1, "sha1")
    assert store.try_claim("o/r", 1, "sha1")
    assert not store.try_claim("o/r", 1, "sha1")


def test_stuck_review_is_reclaimed_after_timeout():
    clock = Clock()
    store = _store(clock, stale_after=900)
    store.try_claim("o/r", 1, "sha1")
    clock.t += 899
    assert not store.try_claim("o/r", 1, "sha1")
    clock.t += 2
    assert store.try_claim("o/r", 1, "sha1")


def test_last_reviewed_sha_ignores_unfinished_reviews():
    clock = Clock()
    store = _store(clock)
    assert store.last_reviewed_sha("o/r", 1) is None
    store.try_claim("o/r", 1, "sha1")
    store.mark_done("o/r", 1, "sha1")
    clock.t += 10
    store.try_claim("o/r", 1, "sha2")
    store.mark_done("o/r", 1, "sha2")
    clock.t += 10
    store.try_claim("o/r", 1, "sha3")  # still in progress
    assert store.last_reviewed_sha("o/r", 1) == "sha2"


def test_concurrent_claims_only_one_wins():
    # Simulates many duplicate webhook deliveries arriving at once.
    store = _store()
    results = []
    barrier = threading.Barrier(10)

    def claim():
        barrier.wait()
        results.append(store.try_claim("o/r", 1, "sha1"))

    threads = [threading.Thread(target=claim) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(True) == 1
