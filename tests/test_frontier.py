from datetime import timedelta

from kifs.storage.database import parse_iso, utcnow
from kifs.storage.frontier import Frontier


def test_new_users_come_first_in_discovery_order(tmp_path):
    frontier = Frontier(tmp_path / "f.json")
    frontier.add_many(["a", "b", "c"])
    assert [frontier.next_user() for _ in range(3)] == ["a", "b", "c"]


def test_adding_a_known_user_is_a_no_op(tmp_path):
    frontier = Frontier(tmp_path / "f.json")
    assert frontier.add("a") is True
    assert frontier.add("a") is False
    assert len(frontier) == 1


def test_crawled_user_comes_back_when_due(tmp_path):
    """The v1 bug: a crawled user was never revisited, so their new games were lost."""
    frontier = Frontier(tmp_path / "f.json", recrawl_hours=24.0)
    frontier.add("a")
    frontier.next_user()
    frontier.mark_crawled("a", games_found=5)

    assert frontier.next_user() is None, "not due yet"

    later = utcnow() + timedelta(hours=25)
    assert frontier.next_user(now=later) == "a"


def test_earliest_due_user_is_served_first(tmp_path):
    frontier = Frontier(tmp_path / "f.json")
    for user_id in ("a", "b"):
        frontier.add(user_id)
        frontier.next_user()
    frontier.mark_crawled("b", 0, recrawl_hours=1.0)
    frontier.mark_crawled("a", 0, recrawl_hours=2.0)
    later = utcnow() + timedelta(hours=3)
    assert frontier.next_user(now=later) == "b"


def test_state_round_trips(tmp_path):
    path = tmp_path / "f.json"
    frontier = Frontier(path)
    frontier.add_many(["a", "b"])
    frontier.next_user()
    frontier.mark_crawled("a", 3)
    frontier.save(force=True)

    reloaded = Frontier(path).load()
    assert len(reloaded) == 2
    assert reloaded.never_crawled == 1
    assert reloaded.users["a"]["games_found"] == 3
    assert parse_iso(reloaded.users["a"]["next_crawl_at"]) is not None


def test_nothing_is_ever_discarded(tmp_path):
    """v1 truncated the queue at 50,000 and dropped the rest silently."""
    frontier = Frontier(tmp_path / "f.json")
    frontier.add_many(str(index) for index in range(60_000))
    assert len(frontier) == 60_000
    assert frontier.never_crawled == 60_000
