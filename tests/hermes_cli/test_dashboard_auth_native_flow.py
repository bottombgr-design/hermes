"""E2E + unit tests for the RFC 8252 native-app (system-browser + loopback +
PKCE) dashboard-auth flow.

Covers:
  * ``native_flow`` broker unit behaviour — PKCE binding, single-use codes,
    expiry, capacity, replay resistance.
  * The full ``/auth/native/authorize`` → ``/auth/callback`` →
    ``/auth/native/token`` round trip in-process against ``StubAuthProvider``.
  * ``/api/status`` capability advertisement (``auth_flows``).
  * Cookieless bearer authentication of a gated route (the whole point of the
    feature — a desktop authenticates REST with ``Authorization: Bearer`` and
    sets/needs no cookie).
  * ``/auth/native/refresh`` token rotation and terminal-expiry semantics.

Run: pytest tests/hermes_cli/test_dashboard_auth_native_flow.py
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from hermes_cli import web_server
from hermes_cli.dashboard_auth import (
    clear_providers,
    register_provider,
)
from hermes_cli.dashboard_auth import native_flow
from hermes_cli.dashboard_auth import routes as auth_routes
from hermes_cli.dashboard_auth.base import ProviderError, RefreshExpiredError, Session
from tests.hermes_cli.conftest_dashboard_auth import StubAuthProvider, _sign


# ---------------------------------------------------------------------------
# PKCE helpers (desktop side)
# ---------------------------------------------------------------------------


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _make_pkce() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` — the desktop's PKCE pair."""
    verifier = _b64url_no_pad(b"desktop-verifier-secret-material-0123456789abcd")
    challenge = _b64url_no_pad(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


# ---------------------------------------------------------------------------
# native_flow broker unit tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_broker():
    native_flow._reset_for_tests()
    auth_routes._reset_native_refresh_replay_for_tests()
    # Snapshot the shared app.state auth fields + provider registry so a test
    # that flips auth_required / registers a stub provider can't leak into a
    # later test file (e.g. the MCP dashboard-oauth suite shares web_server.app).
    prev_required = getattr(web_server.app.state, "auth_required", None)
    prev_host = getattr(web_server.app.state, "bound_host", None)
    prev_port = getattr(web_server.app.state, "bound_port", None)
    yield
    native_flow._reset_for_tests()
    auth_routes._reset_native_refresh_replay_for_tests()
    clear_providers()
    web_server.app.state.auth_required = prev_required
    web_server.app.state.bound_host = prev_host
    web_server.app.state.bound_port = prev_port


def _stub_session(exp_offset: int = 3600) -> Session:
    now = int(time.time())
    return Session(
        user_id="u1",
        email="u1@example.test",
        display_name="U One",
        org_id="org1",
        provider="stub",
        expires_at=now + exp_offset,
        access_token="at-opaque",
        refresh_token="rt-opaque",
    )


def test_broker_happy_path_binds_pkce_and_returns_session():
    verifier, challenge = _make_pkce()
    broker_state = native_flow.register_pending(
        code_challenge=challenge,
        redirect_uri="http://127.0.0.1:53123/callback",
        client_state="client-state-xyz",
    )
    pending = native_flow.get_pending(broker_state)
    assert pending.redirect_uri == "http://127.0.0.1:53123/callback"
    assert pending.client_state == "client-state-xyz"

    sess = _stub_session()
    code = native_flow.complete_pending(broker_state, session=sess)
    redeemed = native_flow.redeem_code(code=code, code_verifier=verifier)
    assert redeemed.access_token == "at-opaque"
    assert redeemed.user_id == "u1"


def test_broker_rejects_wrong_verifier():
    _verifier, challenge = _make_pkce()
    broker_state = native_flow.register_pending(
        code_challenge=challenge,
        redirect_uri="http://127.0.0.1:1/cb",
        client_state="s",
    )
    code = native_flow.complete_pending(broker_state, session=_stub_session())
    with pytest.raises(native_flow.CodeInvalid):
        native_flow.redeem_code(code=code, code_verifier="wrong-verifier")


def test_broker_code_is_single_use():
    verifier, challenge = _make_pkce()
    broker_state = native_flow.register_pending(
        code_challenge=challenge, redirect_uri="http://127.0.0.1:1/cb",
        client_state="s",
    )
    code = native_flow.complete_pending(broker_state, session=_stub_session())
    native_flow.redeem_code(code=code, code_verifier=verifier)
    # Replay must fail — the code was consumed.
    with pytest.raises(native_flow.CodeInvalid):
        native_flow.redeem_code(code=code, code_verifier=verifier)


def test_broker_wrong_verifier_still_consumes_code_no_oracle():
    """A wrong-verifier attempt must not leave the code redeemable — otherwise
    an attacker who steals the loopback code could brute-force the verifier."""
    verifier, challenge = _make_pkce()
    broker_state = native_flow.register_pending(
        code_challenge=challenge, redirect_uri="http://127.0.0.1:1/cb",
        client_state="s",
    )
    code = native_flow.complete_pending(broker_state, session=_stub_session())
    with pytest.raises(native_flow.CodeInvalid):
        native_flow.redeem_code(code=code, code_verifier="wrong")
    # Even the CORRECT verifier now fails: the code was consumed on the first
    # (failed) attempt.
    with pytest.raises(native_flow.CodeInvalid):
        native_flow.redeem_code(code=code, code_verifier=verifier)


def test_broker_pending_expiry():
    verifier, challenge = _make_pkce()
    now = int(time.time())
    broker_state = native_flow.register_pending(
        code_challenge=challenge, redirect_uri="http://127.0.0.1:1/cb",
        client_state="s", now=now,
    )
    # Past the pending TTL, the entry is gone.
    with pytest.raises(native_flow.PendingNotFound):
        native_flow.get_pending(broker_state, now=now + 601)


def test_broker_code_expiry():
    verifier, challenge = _make_pkce()
    now = int(time.time())
    broker_state = native_flow.register_pending(
        code_challenge=challenge, redirect_uri="http://127.0.0.1:1/cb",
        client_state="s", now=now,
    )
    code = native_flow.complete_pending(
        broker_state, session=_stub_session(), now=now,
    )
    with pytest.raises(native_flow.CodeInvalid):
        native_flow.redeem_code(
            code=code, code_verifier=verifier, now=now + 121,
        )


def test_broker_capacity_fails_closed():
    _verifier, challenge = _make_pkce()
    # Fill to capacity.
    for _ in range(native_flow._MAX_ENTRIES):
        native_flow.register_pending(
            code_challenge=challenge, redirect_uri="http://127.0.0.1:1/cb",
            client_state="s",
        )
    with pytest.raises(native_flow.NativeFlowError):
        native_flow.register_pending(
            code_challenge=challenge, redirect_uri="http://127.0.0.1:1/cb",
            client_state="s",
        )


def test_broker_per_ip_pending_cap():
    """One address cannot hog the pending store (public pre-auth route)."""
    _verifier, challenge = _make_pkce()
    for _ in range(native_flow._MAX_PENDING_PER_IP):
        native_flow.register_pending(
            code_challenge=challenge, redirect_uri="http://127.0.0.1:1/cb",
            client_state="s", client_ip="203.0.113.7",
        )
    # The capped IP is refused...
    with pytest.raises(native_flow.NativeFlowError):
        native_flow.register_pending(
            code_challenge=challenge, redirect_uri="http://127.0.0.1:1/cb",
            client_state="s", client_ip="203.0.113.7",
        )
    # ...while a different address still signs in fine.
    assert native_flow.register_pending(
        code_challenge=challenge, redirect_uri="http://127.0.0.1:1/cb",
        client_state="s", client_ip="198.51.100.9",
    )


def test_broker_per_ip_cap_frees_on_expiry():
    """Expired pending entries stop counting against the per-IP cap."""
    _verifier, challenge = _make_pkce()
    now = int(time.time())
    for _ in range(native_flow._MAX_PENDING_PER_IP):
        native_flow.register_pending(
            code_challenge=challenge, redirect_uri="http://127.0.0.1:1/cb",
            client_state="s", client_ip="203.0.113.7", now=now,
        )
    # Past the pending TTL the old entries are GC'd and the IP can retry.
    assert native_flow.register_pending(
        code_challenge=challenge, redirect_uri="http://127.0.0.1:1/cb",
        client_state="s", client_ip="203.0.113.7",
        now=now + native_flow._PENDING_TTL_SECONDS + 1,
    )


# ---------------------------------------------------------------------------
# Route-level E2E against StubAuthProvider
# ---------------------------------------------------------------------------


@pytest.fixture
def gated_client():
    clear_providers()
    register_provider(StubAuthProvider())
    prev_host = getattr(web_server.app.state, "bound_host", None)
    prev_port = getattr(web_server.app.state, "bound_port", None)
    prev_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.bound_host = "fly-app.fly.dev"
    web_server.app.state.bound_port = 443
    web_server.app.state.auth_required = True
    # follow_redirects=False so we can inspect each 302 leg of the flow.
    client = TestClient(
        web_server.app, base_url="https://fly-app.fly.dev",
        follow_redirects=False,
    )
    yield client
    clear_providers()
    web_server.app.state.bound_host = prev_host
    web_server.app.state.bound_port = prev_port
    web_server.app.state.auth_required = prev_required


def _walk_native_login(client, *, redirect_uri, challenge, state="cli-state"):
    """Drive authorize → (stub redirects to callback) → loopback code.

    Returns the ``code`` + ``state`` the gateway put on the loopback redirect.
    """
    # 1. Desktop opens the system browser at /auth/native/authorize.
    r = client.get(
        "/auth/native/authorize",
        params={
            "provider": "stub",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "redirect_uri": redirect_uri,
            "state": state,
        },
    )
    assert r.status_code == 302, r.text
    # Stub's start_login redirects straight to /auth/callback?code=stub_code.
    loc = r.headers["location"]
    parsed = urlparse(loc)
    cb_qs = parse_qs(parsed.query)
    # Carry the gateway PKCE cookie forward (holds broker_state + verifier).
    cookies = r.cookies
    # 2. Browser hits the gateway callback.
    r2 = client.get(
        "/auth/callback",
        params={"code": cb_qs["code"][0], "state": cb_qs["state"][0]},
        cookies=cookies,
    )
    assert r2.status_code == 302, r2.text
    # 3. The callback 302s to the desktop's loopback redirect_uri.
    loop = urlparse(r2.headers["location"])
    assert f"{loop.scheme}://{loop.netloc}" == redirect_uri.rsplit("/", 1)[0] or \
        loop.netloc in redirect_uri
    loop_qs = parse_qs(loop.query)
    # No session cookie must be set on the native callback response.
    set_cookie = r2.headers.get("set-cookie", "")
    assert "hermes_session_at" not in set_cookie, (
        f"native callback must NOT set a session cookie; got {set_cookie!r}"
    )
    return loop_qs["code"][0], loop_qs["state"][0]


def test_native_full_roundtrip_returns_tokens_no_cookie(gated_client):
    verifier, challenge = _make_pkce()
    redirect_uri = "http://127.0.0.1:53999/callback"
    code, state = _walk_native_login(
        gated_client, redirect_uri=redirect_uri, challenge=challenge,
        state="my-cli-state",
    )
    assert state == "my-cli-state"  # client state echoed verbatim

    # 4. Desktop redeems the loopback code + its verifier for tokens.
    r = gated_client.post(
        "/auth/native/token",
        json={"code": code, "code_verifier": verifier},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "Bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["provider"] == "stub"
    assert body["user_id"] == "stub-user-1"
    # No cookie set on the token response either.
    assert "set-cookie" not in {k.lower() for k in r.headers}


def test_native_token_rejects_wrong_verifier(gated_client):
    _verifier, challenge = _make_pkce()
    code, _state = _walk_native_login(
        gated_client, redirect_uri="http://127.0.0.1:53999/cb",
        challenge=challenge,
    )
    r = gated_client.post(
        "/auth/native/token",
        json={"code": code, "code_verifier": "attacker-does-not-have-this"},
    )
    assert r.status_code == 400


def test_native_authorize_rejects_non_loopback_redirect(gated_client):
    _verifier, challenge = _make_pkce()
    r = gated_client.get(
        "/auth/native/authorize",
        params={
            "provider": "stub",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "redirect_uri": "https://evil.example.com/steal",
            "state": "s",
        },
    )
    assert r.status_code == 400
    assert "loopback" in r.json()["detail"].lower()


def test_native_authorize_rejects_localhost_name(gated_client):
    """RFC 8252 §8.3 — loopback IP literals only; `localhost` can be
    re-pointed via the hosts file / a hostile resolver."""
    _verifier, challenge = _make_pkce()
    r = gated_client.get(
        "/auth/native/authorize",
        params={
            "provider": "stub",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "redirect_uri": "http://localhost:53999/cb",
            "state": "s",
        },
    )
    assert r.status_code == 400
    assert "loopback" in r.json()["detail"].lower()


def test_native_authorize_requires_s256(gated_client):
    _verifier, challenge = _make_pkce()
    r = gated_client.get(
        "/auth/native/authorize",
        params={
            "provider": "stub",
            "code_challenge": challenge,
            "code_challenge_method": "plain",
            "redirect_uri": "http://127.0.0.1:1/cb",
            "state": "s",
        },
    )
    assert r.status_code == 400
    assert "s256" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Cookieless bearer auth of a gated route — the core deliverable
# ---------------------------------------------------------------------------


def test_bearer_authenticates_gated_route_without_cookie(gated_client):
    """A desktop that redeemed tokens can call a gated route with only an
    ``Authorization: Bearer`` header — no cookie in the jar."""
    verifier, challenge = _make_pkce()
    code, _state = _walk_native_login(
        gated_client, redirect_uri="http://127.0.0.1:53999/cb",
        challenge=challenge,
    )
    tokens = gated_client.post(
        "/auth/native/token",
        json={"code": code, "code_verifier": verifier},
    ).json()
    at = tokens["access_token"]

    # /api/auth/me is gated; a cookieless request with the bearer must pass
    # and identify the user.
    r = gated_client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {at}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["user_id"] == "stub-user-1"


def test_bearer_ws_ticket_mint_without_cookie(gated_client):
    """The desktop mints a WS ticket with the bearer (no cookie), proving the
    WebSocket path also works cookielessly."""
    verifier, challenge = _make_pkce()
    code, _state = _walk_native_login(
        gated_client, redirect_uri="http://127.0.0.1:53999/cb",
        challenge=challenge,
    )
    at = gated_client.post(
        "/auth/native/token",
        json={"code": code, "code_verifier": verifier},
    ).json()["access_token"]

    r = gated_client.post(
        "/api/auth/ws-ticket",
        headers={"Authorization": f"Bearer {at}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["ticket"]


def test_invalid_bearer_returns_401_envelope(gated_client):
    r = gated_client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert r.status_code == 401
    assert r.json()["error"] == "session_expired"


# ---------------------------------------------------------------------------
# Capability advertisement on /api/status
# ---------------------------------------------------------------------------


def test_status_advertises_native_pkce_flow(gated_client):
    r = gated_client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["auth_required"] is True
    assert "cookie" in body["auth_flows"]
    assert "native_pkce" in body["auth_flows"], (
        "a brokerable OAuth provider must advertise native_pkce so the "
        "desktop can pick the system-browser flow"
    )


def test_status_loopback_mode_has_no_auth_flows():
    clear_providers()
    prev_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    try:
        client = TestClient(web_server.app, base_url="http://127.0.0.1:8080")
        body = client.get("/api/status").json()
        assert body["auth_required"] is False
        assert body["auth_flows"] == []
    finally:
        web_server.app.state.auth_required = prev_required


# ---------------------------------------------------------------------------
# Native refresh
# ---------------------------------------------------------------------------


def test_native_refresh_rotates_tokens(gated_client):
    verifier, challenge = _make_pkce()
    code, _state = _walk_native_login(
        gated_client, redirect_uri="http://127.0.0.1:53999/cb",
        challenge=challenge,
    )
    tokens = gated_client.post(
        "/auth/native/token",
        json={"code": code, "code_verifier": verifier},
    ).json()
    rt = tokens["refresh_token"]

    r = gated_client.post(
        "/auth/native/refresh",
        json={"refresh_token": rt, "provider": "stub"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "Bearer"


def test_native_refresh_dead_token_returns_401(gated_client):
    r = gated_client.post(
        "/auth/native/refresh",
        json={"refresh_token": "garbage-not-a-real-rt", "provider": "stub"},
    )
    assert r.status_code == 401
    assert r.json()["error"] == "session_expired"


def test_native_refresh_reuses_rotated_result_for_same_old_token(gated_client):
    """A stale retry must reuse the first rotated Session, not replay the RT."""

    class CountingProvider(StubAuthProvider):
        name = "counting"
        display_name = "Counting IdP"

        def __init__(self):
            super().__init__(default_ttl=900)
            self.refresh_calls = 0

        def refresh_session(self, *, refresh_token: str) -> Session:
            self.refresh_calls += 1
            return super().refresh_session(refresh_token=refresh_token)

    provider = CountingProvider()
    clear_providers()
    register_provider(provider)

    old_rt = _sign({
        "sub": "stub-user-1",
        "kind": "refresh",
        "exp": int(time.time()) + 3600,
    })

    first = gated_client.post(
        "/auth/native/refresh",
        json={"refresh_token": old_rt, "provider": provider.name},
    )
    second = gated_client.post(
        "/auth/native/refresh",
        json={"refresh_token": old_rt, "provider": provider.name},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert provider.refresh_calls == 1
    assert second.json()["access_token"] == first.json()["access_token"]
    assert second.json()["refresh_token"] == first.json()["refresh_token"]


def test_native_refresh_spoofed_xff_prefix_cannot_split_replay_key(
    gated_client,
):
    """Untrusted leading XFF hops must not bypass refresh coalescing."""

    class CountingProvider(StubAuthProvider):
        name = "xff-counting"
        display_name = "XFF Counting IdP"

        def __init__(self):
            super().__init__(default_ttl=900)
            self.refresh_calls = 0

        def refresh_session(self, *, refresh_token: str) -> Session:
            self.refresh_calls += 1
            return super().refresh_session(refresh_token=refresh_token)

    provider = CountingProvider()
    clear_providers()
    register_provider(provider)

    old_rt = _sign({
        "sub": "stub-user-1",
        "kind": "refresh",
        "exp": int(time.time()) + 3600,
    })
    payload = {
        "refresh_token": old_rt,
        "provider": provider.name,
    }

    first = gated_client.post(
        "/auth/native/refresh",
        json=payload,
        headers={
            "x-forwarded-for": "198.51.100.10, 203.0.113.7",
        },
    )
    second = gated_client.post(
        "/auth/native/refresh",
        json=payload,
        headers={
            "x-forwarded-for": "198.51.100.99, 203.0.113.7",
        },
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert provider.refresh_calls == 1
    assert second.json()["access_token"] == first.json()["access_token"]
    assert second.json()["refresh_token"] == first.json()["refresh_token"]



def test_native_refresh_single_flight_coalesces_concurrent_requests():
    """Overlapping requests with one old rotating RT call the IdP once."""

    class SlowProvider(StubAuthProvider):
        name = "slow"
        display_name = "Slow IdP"

        def __init__(self):
            super().__init__(default_ttl=900)
            self.refresh_calls = 0
            self.calls_lock = threading.Lock()
            self.entered = threading.Event()
            self.release = threading.Event()

        def refresh_session(self, *, refresh_token: str) -> Session:
            with self.calls_lock:
                self.refresh_calls += 1

            self.entered.set()
            if not self.release.wait(timeout=2):
                raise AssertionError("test did not release the simulated IdP")

            now = int(time.time())
            return Session(
                user_id="native-user",
                email="native@example.test",
                display_name="Native User",
                org_id="native-org",
                provider=self.name,
                expires_at=now + 900,
                access_token="rotated-access-token",
                refresh_token="rotated-refresh-token",
            )

    provider = SlowProvider()
    clear_providers()
    register_provider(provider)

    args = ("old-rotating-refresh-token", provider.name, "127.0.0.1")
    cache_key = auth_routes._native_refresh_cache_key(args[0], args[2])

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(auth_routes._refresh_native_session_sync, *args)

        try:
            assert provider.entered.wait(timeout=1)
            second = pool.submit(auth_routes._refresh_native_session_sync, *args)

            # Wait deterministically until both workers reference the same
            # per-token lock. The second worker must not enter the provider.
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                with auth_routes._native_refresh_replay_guard:
                    lock_entry = auth_routes._native_refresh_token_locks.get(
                        cache_key
                    )

                if lock_entry is not None and lock_entry[1] == 2:
                    break

                time.sleep(0.01)
            else:
                raise AssertionError(
                    "second refresh request did not join the per-token lock"
                )

            assert provider.refresh_calls == 1
        finally:
            provider.release.set()

        first_result = first.result(timeout=2)
        second_result = second.result(timeout=2)

    assert provider.refresh_calls == 1
    assert first_result[1] is None
    assert second_result[1] is None
    assert first_result[0] is not None
    assert second_result[0] is not None
    assert first_result[0].refresh_token == second_result[0].refresh_token


def test_native_refresh_keeps_registered_lock_while_waiter_exists():
    """A waiter keeps the per-token lock registered after a transient error."""

    class TransientProvider(StubAuthProvider):
        name = "transient"
        display_name = "Transient IdP"

        def __init__(self):
            super().__init__(default_ttl=900)
            self.refresh_calls = 0
            self.calls_lock = threading.Lock()
            self.first_entered = threading.Event()
            self.release_first = threading.Event()
            self.second_entered = threading.Event()
            self.release_second = threading.Event()

        def refresh_session(self, *, refresh_token: str) -> Session:
            with self.calls_lock:
                self.refresh_calls += 1
                call_number = self.refresh_calls

            if call_number == 1:
                self.first_entered.set()
                if not self.release_first.wait(timeout=2):
                    raise AssertionError("test did not release first refresh")
                raise ProviderError("simulated transient outage")

            if call_number == 2:
                self.second_entered.set()
                if not self.release_second.wait(timeout=2):
                    raise AssertionError("test did not release second refresh")
                raise ProviderError("simulated transient outage")

            if call_number == 3:
                now = int(time.time())
                return Session(
                    user_id="native-user",
                    email="native@example.test",
                    display_name="Native User",
                    org_id="native-org",
                    provider=self.name,
                    expires_at=now + 900,
                    access_token="recovered-access-token",
                    refresh_token="recovered-refresh-token",
                )

            raise AssertionError("unexpected extra provider refresh")

    provider = TransientProvider()
    clear_providers()
    register_provider(provider)

    refresh_token = "old-transient-refresh-token"
    client_ip = "127.0.0.1"
    args = (refresh_token, provider.name, client_ip)
    cache_key = auth_routes._native_refresh_cache_key(
        refresh_token,
        client_ip,
    )

    def wait_for_lock_users(expected: int) -> threading.Lock:
        deadline = time.monotonic() + 1

        while time.monotonic() < deadline:
            with auth_routes._native_refresh_replay_guard:
                entry = auth_routes._native_refresh_token_locks.get(cache_key)
                if entry is not None and entry[1] == expected:
                    return entry[0]

            time.sleep(0.01)

        raise AssertionError(
            f"per-token lock did not reach {expected} referenced users"
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(auth_routes._refresh_native_session_sync, *args)

        try:
            assert provider.first_entered.wait(timeout=1)

            second = pool.submit(auth_routes._refresh_native_session_sync, *args)
            original_lock = wait_for_lock_users(2)

            provider.release_first.set()
            first_result = first.result(timeout=2)

            assert provider.second_entered.wait(timeout=1)

            with auth_routes._native_refresh_replay_guard:
                entry = auth_routes._native_refresh_token_locks.get(cache_key)

            assert entry is not None
            assert entry[0] is original_lock
            assert entry[1] == 1

            third = pool.submit(auth_routes._refresh_native_session_sync, *args)
            assert wait_for_lock_users(2) is original_lock

            time.sleep(0.05)
            assert provider.refresh_calls == 2
        finally:
            provider.release_first.set()
            provider.release_second.set()

        second_result = second.result(timeout=2)
        third_result = third.result(timeout=2)

    assert first_result == (None, provider.name)
    assert second_result == (None, provider.name)
    assert third_result[1] is None
    assert third_result[0] is not None
    assert third_result[0].refresh_token == "recovered-refresh-token"
    assert provider.refresh_calls == 3



def test_native_refresh_negative_cache_absorbs_retry_storm(gated_client):
    """A definitively rejected RT is not resent repeatedly to the IdP."""

    class RejectingProvider(StubAuthProvider):
        name = "rejecting"
        display_name = "Rejecting IdP"

        def __init__(self):
            super().__init__()
            self.refresh_calls = 0

        def refresh_session(self, *, refresh_token: str) -> Session:
            self.refresh_calls += 1
            raise RefreshExpiredError("simulated dead rotating token")

    provider = RejectingProvider()
    clear_providers()
    register_provider(provider)

    payload = {
        "refresh_token": "already-consumed-refresh-token",
        "provider": provider.name,
    }

    first = gated_client.post("/auth/native/refresh", json=payload)
    second = gated_client.post("/auth/native/refresh", json=payload)

    assert first.status_code == 401
    assert second.status_code == 401
    assert first.json()["error"] == "session_expired"
    assert second.json()["error"] == "session_expired"
    assert provider.refresh_calls == 1
