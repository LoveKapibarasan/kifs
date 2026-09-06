from datetime import timedelta

import pytest

from kifs.storage.database import KifuDatabase, parse_iso, utcnow
from kifs.storage.frontier import Frontier


@pytest.fixture
def frontier(tmp_path):
    """The frontier shares the games database's connection and transaction."""
    db = KifuDatabase(tmp_path / "kifs.sqlite3", flush_every_writes=10_000).open()
    yield Frontier(db, recrawl_hours=24.0)
    db.close()


def test_new_users_come_first_in_discovery_order(frontier):
    frontier.add_many(["a", "b", "c"])
    assert [frontier.claim_user() for _ in range(3)] == ["a", "b", "c"]


def test_claiming_leases_the_user_so_a_failed_crawl_cannot_spin(frontier):
    """Selection is a query now, not a pop: without a lease, a user whose crawl
    raised would be handed back on the very next cycle, forever."""
    frontier.add("a")
    assert frontier.claim_user() == "a"
    assert frontier.claim_user() is None, "still leased"

    later = utcnow() + timedelta(minutes=frontier.LEASE_MINUTES + 1)
    assert frontier.claim_user(now=later) == "a", "the lease expires; nothing is lost"


def test_peek_does_not_claim(frontier):
    frontier.add("a")
    assert frontier.peek_user() == "a"
    assert frontier.peek_user() == "a", "peeking must not consume"
    assert frontier.claim_user() == "a"


def test_adding_a_known_user_is_a_no_op(frontier):
    assert frontier.add("a") is True
    assert frontier.add("a") is False
    assert len(frontier) == 1


def test_crawled_user_comes_back_when_due(frontier):
    """The v1 bug: a crawled user was never revisited, so their new games were lost."""
    frontier.add("a")
    frontier.next_user()
    frontier.mark_crawled("a", games_found=5)

    assert frontier.next_user() is None, "not due yet"

    later = utcnow() + timedelta(hours=25)
    assert frontier.next_user(now=later) == "a"


def test_earliest_due_user_is_served_first(frontier):
    for user_id in ("a", "b"):
        frontier.add(user_id)
        frontier.next_user()
    frontier.mark_crawled("b", 0, recrawl_hours=1.0)
    frontier.mark_crawled("a", 0, recrawl_hours=2.0)
    later = utcnow() + timedelta(hours=3)
    assert frontier.next_user(now=later) == "b"


def test_state_round_trips(tmp_path):
    path = tmp_path / "kifs.sqlite3"
    db = KifuDatabase(path, flush_every_writes=10_000).open()
    frontier = Frontier(db)
    frontier.add_many(["a", "b"])
    frontier.next_user()
    frontier.mark_crawled("a", 3)
    db.close()

    reopened = KifuDatabase(path).open()
    reloaded = Frontier(reopened).load()
    assert len(reloaded) == 2
    assert reloaded.never_crawled == 1
    assert reloaded.get("a")["games_found"] == 3
    assert parse_iso(reloaded.get("a")["next_crawl_at"]) is not None
    reopened.close()


def test_nothing_is_ever_discarded(frontier):
    """v1 truncated the queue at 50,000 and dropped the rest silently."""
    frontier.add_many(str(index) for index in range(60_000))
    assert len(frontier) == 60_000
    assert frontier.never_crawled == 60_000
