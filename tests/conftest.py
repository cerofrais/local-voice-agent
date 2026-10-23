import os
import uuid
from pathlib import Path

import httpx
import pytest

from agent.call.session import CallSession
from agent.crm import HttpCRM, MockCRM, Outbox, ResilientCRM
from agent.crm.mock import DEFAULT_DESTINATIONS
from agent.crm.models import AgentConfig


def _live_settings() -> tuple[str, str]:
    from agent.config import get_settings

    s = get_settings()
    return os.environ.get("TEST_CRM_BASE_URL", s.crm_base_url), os.environ.get("TEST_CRM_API_KEY", s.crm_api_key)


def _live_available() -> bool:
    base, key = _live_settings()
    if not (base and key):
        return False
    try:
        r = httpx.get(base.rstrip("/") + "/config", headers={"Authorization": f"Bearer {key}"}, timeout=2)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


LIVE = _live_available()


def fake_phone() -> str:
    """Unique, obviously fake number so live test leads are easy to find and delete (+91 90000 xxxxx)."""
    return "+9190000" + f"{uuid.uuid4().int % 100000:05d}"


@pytest.fixture(params=["mock", pytest.param("http", marks=pytest.mark.skipif(not LIVE, reason="live CRM not reachable"))])
async def crm(request, tmp_path):
    if request.param == "mock":
        yield MockCRM()
    else:
        base, key = _live_settings()
        client = HttpCRM(base, key)
        yield client
        await client.aclose()


@pytest.fixture
def mock():
    return MockCRM()


@pytest.fixture
def fresh_phone():
    return fake_phone()


@pytest.fixture
def make_session(mock):
    def _make(crm=None, *, phone=None, contact=None, config=None) -> CallSession:
        return CallSession(
            crm=crm or mock,
            config=config or AgentConfig(),
            destinations=list(DEFAULT_DESTINATIONS),
            caller_phone=phone,
            contact=contact,
        )

    return _make


@pytest.fixture
def resilient(tmp_path: Path, mock):
    return ResilientCRM(mock, Outbox(tmp_path / "outbox"))
