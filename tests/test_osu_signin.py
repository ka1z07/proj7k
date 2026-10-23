"""Sign in with osu!, through the catalog site, and the signed-in account's rank and pp (ADR-0041)."""

import json
import threading
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlsplit

import pytest

from proj7k.catalog.oauth import OAuthBroker, local_state, parse_state
from proj7k.catalog.server import make_server
from proj7k.catalog.store import CatalogStore
from proj7k.dashboard.player import OsuAccount, online_profile

ME = {
    "id": 123, "username": "inawah", "country_code": "CN",
    "statistics": {
        "pp": 3456.7, "global_rank": 9876, "country_rank": 321, "hit_accuracy": 96.5, "play_count": 4000,
        "level": {"current": 88, "progress": 41},
        "variants": [
            {"mode": "mania", "variant": "4k", "country_rank": None, "global_rank": None, "pp": 0},
            {"mode": "mania", "variant": "7k", "country_rank": 12, "global_rank": 456, "pp": 3300.2},
        ],
    },
    "rank_history": {"mode": "mania", "data": [10000, 9950, 9876]},
}


def test_online_profile_keeps_rank_and_pp():
    p = online_profile(ME)
    assert p["username"] == "inawah" and p["mania"]["pp"] == 3456.7 and p["mania"]["global_rank"] == 9876
    assert p["variants"]["7k"] == {"pp": 3300.2, "global_rank": 456, "country_rank": 12}
    assert p["variants"]["4k"]["global_rank"] is None
    assert p["rank_history"] == [10000, 9950, 9876]
    assert online_profile({"id": 1})["variants"] == {}


def test_state_carries_only_a_local_port_and_a_nonce():
    nonce = "a" * 24
    assert parse_state(local_state(7770, nonce)) == (7770, nonce)
    for port, n in ((80, nonce), ("x", nonce), (7770, "short"), (7770, "bad.nonce" * 3), (7770, None)):
        assert local_state(port, n) is None
    assert parse_state("evil.com/x") is None


# --- the site's half -----------------------------------------------------------------------------------------


class FakeOsu:
    def __init__(self):
        self.posts = []

    def __call__(self, url, form):
        self.posts.append((url, form))
        if form.get("code") == "bad" or form.get("refresh_token") == "revoked":
            raise urllib.error.HTTPError(url, 401, "unauthorized", {}, None)
        return {"access_token": "AT", "refresh_token": "RT", "expires_in": 86400, "token_type": "Bearer"}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _call(url, data=None):
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None,
                                 headers={"Content-Type": "application/json"})
    try:
        with opener.open(req) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


@pytest.fixture()
def site():
    osu = FakeOsu()
    broker = OAuthBroker("42", "secret", "https://7k.example/oauth/callback", post=osu)
    store = CatalogStore()
    server = make_server(store, "127.0.0.1", 0, oauth=broker)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", osu
    server.shutdown()
    server.server_close()
    store.close()


def test_site_runs_the_authorization_code_flow(site):
    base, osu = site
    nonce = "n" * 24
    status, headers, _ = _call(f"{base}/oauth/start?port=7770&nonce={nonce}")
    assert status == 302
    auth = urlsplit(headers["Location"])
    q = {k: v[0] for k, v in parse_qs(auth.query).items()}
    assert auth.netloc == "osu.ppy.sh" and auth.path == "/oauth/authorize"
    assert q == {"client_id": "42", "redirect_uri": "https://7k.example/oauth/callback", "response_type": "code",
                 "scope": "public identify", "state": f"7770.{nonce}"}
    assert "secret" not in headers["Location"]

    status, headers, _ = _call(f"{base}/oauth/callback?code=C0DE&state=7770.{nonce}")
    assert status == 302 and headers["Cache-Control"] == "no-store"
    back = urlsplit(headers["Location"])
    assert back.netloc == "127.0.0.1:7770" and back.path == "/oauth/done"
    bq = {k: v[0] for k, v in parse_qs(back.query).items()}
    assert bq["nonce"] == nonce and "AT" not in headers["Location"]
    assert osu.posts[0][1] == {"client_id": "42", "client_secret": "secret", "code": "C0DE",
                               "grant_type": "authorization_code", "redirect_uri": "https://7k.example/oauth/callback"}

    status, headers, body = _call(f"{base}/oauth/redeem", {"handoff": bq["handoff"]})
    assert status == 200 and json.loads(body)["access_token"] == "AT" and headers["Cache-Control"] == "no-store"
    assert _call(f"{base}/oauth/redeem", {"handoff": bq["handoff"]})[0] == 404   # once only

    status, _, body = _call(f"{base}/oauth/refresh", {"refresh_token": "RT"})
    assert status == 200 and json.loads(body)["refresh_token"] == "RT"
    assert _call(f"{base}/oauth/refresh", {"refresh_token": "revoked"})[0] == 401


def test_site_refuses_bad_states_and_reports_denials(site):
    base, osu = site
    assert _call(f"{base}/oauth/start?port=80&nonce={'n' * 24}")[0] == 400
    assert _call(f"{base}/oauth/callback?code=x&state=evil")[0] == 400
    status, headers, _ = _call(f"{base}/oauth/callback?error=access_denied&state=7770.{'n' * 24}")
    assert status == 302 and "error=access_denied" in headers["Location"] and not osu.posts
    status, headers, _ = _call(f"{base}/oauth/callback?code=bad&state=7770.{'n' * 24}")
    assert "error=exchange_failed" in headers["Location"]
    assert _call(f"{base}/oauth/redeem", {"handoff": "nope"})[0] == 404


def test_site_without_a_client_answers_503():
    store = CatalogStore()
    server = make_server(store, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert _call(f"http://127.0.0.1:{server.server_address[1]}/oauth/start?port=7770&nonce={'n' * 24}")[0] == 503
    finally:
        server.shutdown()
        server.server_close()
        store.close()


# --- the app's half ------------------------------------------------------------------------------------------


class FakeNet:
    """The catalog site and osu!'s API, as the dashboard sees them."""

    def __init__(self):
        self.calls = []
        self.me_status = 200

    def __call__(self, url, headers, body):
        self.calls.append((url, headers, json.loads(body) if body else None))
        if url.endswith("/oauth/redeem"):
            return json.dumps({"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600}).encode()
        if url.endswith("/oauth/refresh"):
            return json.dumps({"access_token": "AT2", "refresh_token": "RT2", "expires_in": 3600}).encode()
        if url.endswith("/api/v2/me/mania"):
            if self.me_status != 200:
                raise urllib.error.HTTPError(url, self.me_status, "x", {}, None)
            return json.dumps(ME).encode()
        raise AssertionError(url)


def _account(tmp_path, net, now):
    return OsuAccount(token_path=tmp_path / "osu_account.json", port=7770, catalog="https://7k.example",
                      api="https://osu.example", http=net, clock=lambda: now[0])


def test_account_signs_in_reads_rank_and_refreshes(tmp_path):
    net, now = FakeNet(), [1000.0]
    acct = _account(tmp_path, net, now)
    st = acct.status()
    assert st["signed_in"] is False
    q = {k: v[0] for k, v in parse_qs(urlsplit(st["login_url"]).query).items()}
    assert st["login_url"].startswith("https://7k.example/oauth/start?") and q["port"] == "7770"

    assert acct.finish("wrong-nonce", "H") is not None      # not started here
    assert acct.finish(q["nonce"], "H") is None
    assert net.calls[0][2] == {"handoff": "H"}
    saved = json.loads((tmp_path / "osu_account.json").read_text())
    assert saved["access_token"] == "AT1" and saved["user"] == {"id": 123, "username": "inawah"}
    assert acct.finish(q["nonce"], "H") is not None         # a nonce works once

    st = acct.status()
    assert st["signed_in"] and st["profile"]["variants"]["7k"]["global_rank"] == 456
    calls = len(net.calls)
    acct.status()
    assert len(net.calls) == calls                          # cached

    now[0] += 3600 + 600                                    # token and cache both expired
    st = acct.status()
    assert any(c[0].endswith("/oauth/refresh") and c[2] == {"refresh_token": "RT1"} for c in net.calls)
    assert net.calls[-1][1]["Authorization"] == "Bearer AT2" and st["profile"]["id"] == 123

    acct.status()
    acct.logout()
    assert not (tmp_path / "osu_account.json").exists() and acct.status()["signed_in"] is False


def test_account_offline_keeps_the_sign_in_and_revoked_signs_out(tmp_path):
    net, now = FakeNet(), [1000.0]
    acct = _account(tmp_path, net, now)
    nonce = parse_qs(urlsplit(acct.login_url()).query)["nonce"][0]
    assert acct.finish(nonce, "H") is None
    now[0] += 700
    net.me_status = 503
    st = acct.status()
    assert st["signed_in"] and st["profile"] is None and st["user"]["username"] == "inawah"
    now[0] += 200
    net.me_status = 401
    assert acct.status()["signed_in"] is False


def test_account_reports_a_denied_sign_in(tmp_path):
    acct = _account(tmp_path, FakeNet(), [0.0])
    nonce = parse_qs(urlsplit(acct.login_url()).query)["nonce"][0]
    assert "access_denied" in acct.finish(nonce, None, "access_denied")
    assert acct.status()["signed_in"] is False


def test_dashboard_routes(tmp_path):
    import asyncio

    from test_dashboard import _Server

    async def _run():
        s = _Server(tmp_path)
        net = FakeNet()
        s.server.osu_account = _account(tmp_path, net, [1000.0])
        await s.server.start()
        try:
            st = json.loads((await s.get("/api/me/online"))[2])
            assert st["signed_in"] is False
            nonce = parse_qs(urlsplit(st["login_url"]).query)["nonce"][0]
            status, headers, body = await s.get(f"/oauth/done?nonce={nonce}&handoff=H")
            assert status == 200 and headers["Content-Type"].startswith("text/html") and "osu!" in body.decode()
            st = json.loads((await s.get("/api/me/online"))[2])
            assert st["signed_in"] and st["profile"]["variants"]["7k"]["pp"] == 3300.2
            assert json.loads((await s.get("/api/me/online/logout"))[2])["signed_in"] is False
            status, _, body = await s.get("/oauth/done?nonce=forged&handoff=H")
            assert status == 200 and not (tmp_path / "osu_account.json").exists()
        finally:
            await s.server.stop()

    asyncio.run(_run())
