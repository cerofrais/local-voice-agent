"""Everything that has to be known before the first word: CRM config, destinations, who is calling."""

import asyncio
from typing import Any

import httpx
from loguru import logger

from agent.call.session import CallSession
from agent.crm.client import CRMClient
from agent.crm.mock import DEFAULT_DESTINATIONS
from agent.crm.models import AgentConfig, ContactMatch, CRMError, Destination
from agent.lead import parsers as p

STARTUP_BUDGET_S = 4.0  # the caller is waiting in silence; never hold the greeting longer than this


async def plivo_caller_number(call_uuid: str, auth_id: str, auth_token: str) -> str:
    """Plivo's stream handshake carries no caller number; ask the REST API for the live call."""
    if not (call_uuid and auth_id and auth_token):
        return ""
    url = f"https://api.plivo.com/v1/Account/{auth_id}/Call/{call_uuid}/"
    try:
        async with httpx.AsyncClient(timeout=2.5) as c:
            for params in ({"status": "live"}, {}):
                r = await c.get(url, params=params, auth=(auth_id, auth_token))
                if r.status_code == 200:
                    j = r.json()
                    # The direction decides which leg is the person calling us.
                    return str(j.get("from") or j.get("from_number") or "")
    except (httpx.HTTPError, ValueError) as e:
        logger.warning(f"could not look up the caller ID for call {call_uuid}: {e}")
    return ""


def caller_phone_from(runner_args: Any, dev_phone: str = "", looked_up: str = "") -> str | None:
    """Caller ID: telephony lookup first, then the request body, then DEV_CALLER_PHONE for WebRTC tests."""
    body = getattr(runner_args, "body", None) or {}
    raw = looked_up
    if not raw and isinstance(body, dict):
        raw = str(body.get("caller_phone") or body.get("from") or body.get("From") or "")
    call_data = getattr(runner_args, "call_data", None)
    if not raw and call_data is not None:
        raw = str((call_data.get("from") if hasattr(call_data, "get") else "") or "")
    raw = raw or dev_phone
    if not raw:
        return None
    try:
        return p.normalize_phone(raw)
    except ValueError:
        logger.warning(f"ignoring caller phone {raw!r}: not a valid number")
        return None


async def start_session(crm: CRMClient, caller_phone: str | None, *, duplicate_window_days: int = 30) -> CallSession:
    async def cfg() -> AgentConfig:
        return await crm.get_config()

    async def dests() -> list[Destination]:
        return await crm.list_destinations()

    async def contact() -> ContactMatch | None:
        return await crm.lookup_contact(caller_phone) if caller_phone else None

    tasks = [asyncio.create_task(c()) for c in (cfg, dests, contact)]
    done, pending = await asyncio.wait(tasks, timeout=STARTUP_BUDGET_S)
    for t in pending:
        t.cancel()
    trouble = bool(pending)

    def pick(t: asyncio.Task, default: Any, what: str) -> Any:
        nonlocal trouble
        if t not in done:
            logger.warning(f"CRM {what} timed out; continuing with defaults")
            return default
        try:
            return t.result()
        except CRMError as e:
            trouble = True
            logger.warning(f"CRM {what} failed ({e}); continuing with defaults")
            return default

    config = pick(tasks[0], AgentConfig(), "config")
    destinations = pick(tasks[1], list(DEFAULT_DESTINATIONS), "destinations") or list(DEFAULT_DESTINATIONS)
    match = pick(tasks[2], None, "contact lookup")
    session = CallSession(
        crm=crm,
        config=config,
        destinations=destinations,
        caller_phone=caller_phone,
        contact=match,
        duplicate_window_days=duplicate_window_days,
    )
    session.crm_trouble = trouble
    logger.info(
        f"call {session.call_id}: caller={caller_phone or 'unknown'} "
        f"contact={'yes' if match else 'no'} crm_ok={not trouble} agent={config.name}"
    )
    return session
