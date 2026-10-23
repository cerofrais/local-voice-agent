import httpx
import pytest

from agent.call import startup
from agent.call.startup import caller_phone_from, plivo_caller_number


class _Args:
    def __init__(self, body=None, call_data=None):
        self.body, self.call_data = body, call_data


def test_caller_id_precedence():
    assert caller_phone_from(_Args(), "+919000000001") == "+919000000001"  # WebRTC dev phone
    assert caller_phone_from(_Args({"From": "9876543210"}), "+919000000001") == "+919876543210"
    assert caller_phone_from(_Args(), "+919000000001", looked_up="+919111122222") == "+919111122222"  # telephony wins
    assert caller_phone_from(_Args(call_data={"from": "919822233333"}), "") == "+919822233333"
    assert caller_phone_from(_Args(), "garbage") is None


async def test_plivo_lookup_uses_the_live_call(monkeypatch):
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["auth"] = str(req.url), req.headers.get("authorization")
        return httpx.Response(200, json={"from": "919876543210", "to": "918071234567"})

    real = httpx.AsyncClient
    monkeypatch.setattr(startup.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    assert await plivo_caller_number("uuid-1", "MAXXXX", "tok") == "919876543210"
    assert "/Account/MAXXXX/Call/uuid-1/" in seen["url"] and seen["auth"].startswith("Basic ")


async def test_plivo_lookup_degrades_quietly(monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(startup.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(404)), **kw))
    assert await plivo_caller_number("uuid-1", "MAXXXX", "tok") == ""
    assert await plivo_caller_number("", "MAXXXX", "tok") == ""
    assert await plivo_caller_number("uuid-1", "", "") == ""
