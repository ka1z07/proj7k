"""
"Sign in with osu!" for the desktop app (ADR-0041): the catalog site holds the OAuth client secret the app can't ship.

    GET  /oauth/start?port=<local port>&nonce=<nonce>   redirects to osu!'s authorization page
    GET  /oauth/callback?code=&state=                   osu! sends the player back here; the code is exchanged for
                                                        tokens, which wait under a one-time handoff code, and the
                                                        browser goes on to http://127.0.0.1:<port>/oauth/done
    POST /oauth/redeem   {"handoff": ...}               the app takes its tokens (once, within HANDOFF_TTL_S)
    POST /oauth/refresh  {"refresh_token": ...}         new tokens for an expired access token

The site keeps no token past the handoff: tokens live on the player's machine. The tokens never travel in a URL; the
browser only carries the handoff code to the player's own machine, and the app checks the nonce it started with.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

OSU_BASE = "https://osu.ppy.sh"
#: What the app asks for: the player's identity (`/me`) and public data.
SCOPES = "public identify"
HANDOFF_TTL_S = 300.0
NONCE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

Post = Callable[[str, Dict[str, str]], Dict[str, Any]]


class OAuthError(Exception):
    pass


def _post_form(url: str, form: Dict[str, str]) -> Dict[str, Any]:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _tokens(data: Dict[str, Any]) -> Dict[str, Any]:
    if not data.get("access_token"):
        raise OAuthError("osu! returned no access token")
    return {"access_token": data["access_token"], "refresh_token": data.get("refresh_token"),
            "expires_in": int(data.get("expires_in") or 0), "token_type": data.get("token_type", "Bearer")}


def local_state(port: Any, nonce: Any) -> Optional[str]:
    """The `state` osu! carries back: where the player's app listens and the nonce it started with."""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if not 1024 <= port <= 65535 or not isinstance(nonce, str) or not NONCE.match(nonce):
        return None
    return f"{port}.{nonce}"


def parse_state(state: str) -> Optional[Tuple[int, str]]:
    port, _, nonce = (state or "").partition(".")
    return (int(port), nonce) if local_state(port, nonce) else None


class OAuthBroker:
    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, post: Post = _post_form,
                 base: str = OSU_BASE, clock: Callable[[], float] = time.monotonic) -> None:
        self.client_id, self.client_secret, self.redirect_uri = client_id, client_secret, redirect_uri
        self.post, self.base, self.clock = post, base, clock
        self._handoffs: Dict[str, Tuple[float, Dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def authorize_url(self, state: str) -> str:
        return f"{self.base}/oauth/authorize?" + urllib.parse.urlencode({
            "client_id": self.client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
            "scope": SCOPES, "state": state})

    def exchange(self, code: str) -> str:
        """Trade osu!'s authorization code for tokens; the handoff code the app redeems them with."""
        tokens = _tokens(self.post(f"{self.base}/oauth/token", {
            "client_id": self.client_id, "client_secret": self.client_secret, "code": code,
            "grant_type": "authorization_code", "redirect_uri": self.redirect_uri}))
        handoff = secrets.token_urlsafe(32)
        with self._lock:
            now = self.clock()
            for k in [k for k, (t, _) in self._handoffs.items() if t <= now]:
                del self._handoffs[k]
            self._handoffs[handoff] = (now + HANDOFF_TTL_S, tokens)
        return handoff

    def redeem(self, handoff: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            hit = self._handoffs.pop(handoff, None) if isinstance(handoff, str) else None
        return hit[1] if hit and self.clock() < hit[0] else None

    def refresh(self, refresh_token: str) -> Dict[str, Any]:
        return _tokens(self.post(f"{self.base}/oauth/token", {
            "client_id": self.client_id, "client_secret": self.client_secret, "refresh_token": refresh_token,
            "grant_type": "refresh_token", "scope": SCOPES}))
