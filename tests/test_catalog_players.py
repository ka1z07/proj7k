"""Online rank and pp for the desktop app, through the catalog site (ADR-0041)."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from proj7k.catalog.players import PlayerProfiles, PlayerUnavailable, osu_user_fetcher, public_profile
from proj7k.catalog.server import make_server
from proj7k.catalog.store import CatalogStore
from proj7k.dashboard.player import OnlineProfile

USER = {
    "id": 123, "username": "inawah", "country_code": "CN", "avatar_url": "https://a.ppy.sh/123",
    "statistics": {
        "pp": 3456.7, "global_rank": 9876, "country_rank": 321, "hit_accuracy": 96.5, "play_count": 4000,
        "play_time": 720000, "level": {"current": 88, "progress": 41}, "ranked_score": 10, "maximum_combo": 2000,
        "grade_counts": {"ss": 1, "s": 20, "a": 300},
        "variants": [
            {"mode": "mania", "variant": "4k", "country_rank": None, "global_rank": None, "pp": 0},
            {"mode": "mania", "variant": "7k", "country_rank": 12, "global_rank": 456, "pp": 3300.2},
        ],
    },
    "rank_history": {"mode": "mania", "data": [10000, 9950, 9876]},
}


def test_public_profile_keeps_the_rank_and_pp_fields():
    p = public_profile(USER)
    assert p["username"] == "inawah" and p["country_code"] == "CN"
    assert p["mania"]["pp"] == 3456.7 and p["mania"]["global_rank"] == 9876 and p["mania"]["accuracy"] == 96.5
    assert p["variants"]["7k"] == {"pp": 3300.2, "global_rank": 456, "country_rank": 12}
    assert p["variants"]["4k"]["global_rank"] is None   # unranked in that keycount
    assert p["rank_history"] == [10000, 9950, 9876]
    assert public_profile({"id": 1})["variants"] == {}   # a player who never played mania


def test_profiles_are_cached_and_failures_remembered_briefly():
    now = [0.0]
    calls = []

    def fetch(uid):
        calls.append(uid)
        if uid == 2:
            raise OSError("osu! down")
        return None if uid == 3 else USER

    profiles = PlayerProfiles(fetch, ttl_s=600, error_ttl_s=60, clock=lambda: now[0])
    assert profiles.get(1)["id"] == 123
    assert profiles.get(1)["id"] == 123
    assert profiles.get(3) is None
    with pytest.raises(PlayerUnavailable):
        profiles.get(2)
    with pytest.raises(PlayerUnavailable):
        profiles.get(2)
    assert calls == [1, 3, 2]
    now[0] = 61.0
    with pytest.raises(PlayerUnavailable):
        profiles.get(2)
    profiles.get(1)
    assert calls == [1, 3, 2, 2]
    now[0] = 700.0
    profiles.get(1)
    assert calls[-1] == 1


def test_without_an_oauth_client_profiles_are_unavailable():
    with pytest.raises(PlayerUnavailable):
        PlayerProfiles(None).get(1)


def test_fetcher_asks_osu_for_the_mania_profile_and_maps_404_to_none():
    class Client:
        def __init__(self):
            self.asked = []

        def get(self, path, params=None, retries=4):
            self.asked.append((path, params))
            if path.startswith("users/404/"):
                raise urllib.error.HTTPError(path, 404, "not found", {}, None)
            return USER

    client = Client()
    fetch = osu_user_fetcher(client)
    assert fetch(123) is USER
    assert fetch(404) is None
    assert client.asked[0] == ("users/123/mania", [("key", "id")])


@pytest.fixture()
def site():
    def fetch(uid):
        if uid == 9:
            raise OSError("down")
        return None if uid == 404 else {**USER, "id": uid}

    store = CatalogStore()
    server = make_server(store, "127.0.0.1", 0, players=PlayerProfiles(fetch))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    store.close()


def _status(url):
    try:
        with urllib.request.urlopen(url) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read())


def test_site_answers_player_profiles(site):
    status, headers, body = _status(f"{site}/api/players/123")
    assert status == 200 and body["variants"]["7k"]["global_rank"] == 456
    assert "max-age=600" in headers["Cache-Control"]
    assert _status(f"{site}/api/players/404")[0] == 404
    assert _status(f"{site}/api/players/9")[0] == 503
    assert _status(f"{site}/api/players/abc")[0] == 404


def test_site_without_credentials_answers_503():
    store = CatalogStore()
    server = make_server(store, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert _status(f"http://127.0.0.1:{server.server_address[1]}/api/players/1")[0] == 503
    finally:
        server.shutdown()
        server.server_close()
        store.close()


def test_dashboard_reads_the_profile_through_the_catalog_and_hides_failures():
    asked = []

    def fetch(url):
        asked.append(url)
        if url.endswith("/7"):
            raise OSError("offline")
        return json.dumps(public_profile(USER)).encode()

    online = OnlineProfile(base="https://catalog.example/", fetch=fetch)
    assert online.get(123) == {"available": True, "profile": public_profile(USER)}
    online.get(123)
    assert asked == ["https://catalog.example/api/players/123"]
    assert online.get(7) == {"available": False, "reason": "offline"}
    assert online.get(-1) == {"available": False, "reason": "no_user"}
