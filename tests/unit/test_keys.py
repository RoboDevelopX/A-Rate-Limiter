from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from mlratelimit.keys import ClientIP, api_key, user_id


@dataclass
class Client:
    host: str


@dataclass
class FakeRequest:
    headers: dict[str, str] = field(default_factory=dict)
    client: Client | None = None
    state: Any = field(default_factory=SimpleNamespace)


def req(peer: str | None, forwarded: str | None = None) -> FakeRequest:
    headers = {"x-forwarded-for": forwarded} if forwarded is not None else {}
    return FakeRequest(headers, Client(peer) if peer else None)


PROXIES = ["10.0.0.0/8", "192.168.1.1"]


@pytest.mark.parametrize(
    ("peer", "forwarded", "trusted", "expected"),
    [
        ("203.0.113.7", None, [], "ip:203.0.113.7"),
        # Untrusted peers cannot choose their key by sending the header.
        ("203.0.113.7", "1.2.3.4", [], "ip:203.0.113.7"),
        ("203.0.113.7", "1.2.3.4", PROXIES, "ip:203.0.113.7"),
        # Behind a trusted proxy: the right-most address that is not a proxy is the client.
        ("10.0.0.5", "203.0.113.7", PROXIES, "ip:203.0.113.7"),
        ("10.0.0.5", "6.6.6.6, 203.0.113.7, 192.168.1.1", PROXIES, "ip:203.0.113.7"),
        # Every hop is a proxy: take the left-most.
        ("10.0.0.5", "10.0.0.9, 10.0.0.8", PROXIES, "ip:10.0.0.9"),
        # A malformed chain is not trusted.
        ("10.0.0.5", "203.0.113.7, not-an-ip", PROXIES, "ip:10.0.0.5"),
        ("10.0.0.5", " , ", PROXIES, "ip:10.0.0.5"),
        ("unix-socket", "203.0.113.7", PROXIES, "ip:unix-socket"),
        ("2001:db8::1", None, PROXIES, "ip:2001:db8::1"),
    ],
)
def test_client_ip(peer: str, forwarded: str | None, trusted: list[str], expected: str) -> None:
    assert ClientIP(trusted)(req(peer, forwarded)) == expected


def test_client_ip_without_peer() -> None:
    assert ClientIP()(req(None)) is None


def test_api_key_is_hashed() -> None:
    key = api_key()(FakeRequest({"X-API-Key": "secret-value"}))
    assert key is not None
    assert key.startswith("key:")
    assert "secret-value" not in key
    assert key == api_key()(FakeRequest({"X-API-Key": "secret-value"}))
    assert api_key()(FakeRequest()) is None
    assert api_key("Authorization")(FakeRequest({"Authorization": "x"})) is not None


def test_user_id_reads_request_state() -> None:
    request = FakeRequest()
    assert user_id()(request) is None
    request.state.user_id = 42
    assert user_id()(request) == "user:42"
