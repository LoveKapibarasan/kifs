import httpx
import pytest

from kifs.clients.kishin import KifNotAvailable, KishinAnalyticsClient
from kifs.clients.shogiwars import AuthenticationError, ShogiWarsClient, parse_mypage

RANKING_HTML = """
<a href="/users/mypage/junjun3449?locale=ja">junjun3449</a>
<a href="/users/mypage/massuan?locale=ja">massuan</a>
<a href="/users/mypage/junjun3449?locale=ja">junjun3449</a>
"""

HISTORY_HTML = """
<a href="https://kishin-analytics.heroz.jp/?wars_game_id=a-b-20260101_000000&x=1">分析</a>
<a href="https://kishin-analytics.heroz.jp/?wars_game_id=c-d-20260102_000000&x=1">分析</a>
"""

MYPAGE_HTML = """
<table id="user_dankyu">
<tr><th>10分</th><td class="dankyu">四段</td><td></td></tr>
<tr><th>3分</th><td class="dankyu">三段</td><td>最高: 四段 <a href="#">3.25段 </a></td></tr>
</table>
"""


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_ranking_user_ids_are_deduplicated_in_order():
    async with _client(lambda r: httpx.Response(200, text=RANKING_HTML)) as http:
        ids = await ShogiWarsClient(http, "cookie").fetch_ranking_user_ids(1)
    assert ids == ["junjun3449", "massuan"]


async def test_history_game_ids():
    async with _client(lambda r: httpx.Response(200, text=HISTORY_HTML)) as http:
        ids = await ShogiWarsClient(http, "cookie").fetch_game_ids("u", "sb")
    assert ids == ["a-b-20260101_000000", "c-d-20260102_000000"]


async def test_expired_cookie_is_reported_not_swallowed():
    """A 401 means the session died; the service must say so rather than log a
    generic fetch error and keep crawling nothing."""
    async with _client(lambda r: httpx.Response(401)) as http:
        with pytest.raises(AuthenticationError):
            await ShogiWarsClient(http, "stale").fetch_ranking_user_ids(1)


async def test_history_transport_error_returns_empty():
    def boom(request):
        raise httpx.ConnectError("down")

    async with _client(boom) as http:
        assert await ShogiWarsClient(http, "c").fetch_game_ids("u", "sb") == []


def test_parse_mypage():
    modes = parse_mypage(MYPAGE_HTML)
    assert modes["3m"]["dankyu"] == "三段"
    assert modes["3m"]["rating"] == 3.25
    assert modes["3m"]["highest"] == "四段"
    assert modes["10m"]["dankyu"] == "四段"


async def test_kif_404_is_retryable_not_fatal():
    async with _client(lambda r: httpx.Response(404)) as http:
        client = KishinAnalyticsClient(http, "sessionid")
        with pytest.raises(KifNotAvailable):
            await client.fetch_kif("a-b-20260101_000000")


async def test_kif_success():
    payload = {"game_id": "a-b-20260101_000000", "kif": "先手：a\n"}
    async with _client(lambda r: httpx.Response(200, json=payload)) as http:
        text = await KishinAnalyticsClient(http, "sessionid").fetch_kif("a-b-20260101_000000")
    assert text.startswith("先手：a")


async def test_empty_kif_field_is_retryable():
    async with _client(lambda r: httpx.Response(200, json={"kif": ""})) as http:
        with pytest.raises(KifNotAvailable):
            await KishinAnalyticsClient(http, "sessionid").fetch_kif("a-b-1")
