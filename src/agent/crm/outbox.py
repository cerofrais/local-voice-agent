"""Never lose a lead because the CRM blinked.

`ResilientCRM` forwards reads straight through (a failed read raises CRMUnavailable so the tool can
tell the caller a consultant will call back). Writes that fail with CRMUnavailable are saved as one
JSON file each under data/outbox/ and replayed in order with the SAME idempotency key, so a replay can
never create a duplicate. A write that depends on a queued one (a call log pointing at a lead that has
not reached the CRM yet) carries a `queued:<key>` placeholder that is swapped for the real id on replay.
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from agent.crm.client import CRMClient
from agent.crm.models import (
    AgentConfig,
    CallRef,
    ContactMatch,
    CRMError,
    CRMUnavailable,
    Destination,
    EnquiryRef,
    OpenEnquiry,
    Payload,
    TaskRef,
)

QUEUED = "queued:"


def _walk(obj: Any, fn):
    if isinstance(obj, dict):
        return {k: _walk(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, fn) for v in obj]
    return fn(obj)


class Outbox:
    def __init__(self, directory: Path):
        self.dir = directory
        self.dead = directory / "dead"
        self._resolved_path = directory / "_resolved.json"
        directory.mkdir(parents=True, exist_ok=True)

    def entries(self) -> list[Path]:
        return sorted(p for p in self.dir.glob("*.json") if not p.name.startswith("_"))

    def __len__(self) -> int:
        return len(self.entries())

    def put(self, op: str, args: dict[str, Any], key: str) -> None:
        name = f"{time.time_ns()}-{key.replace(':', '_').replace('/', '_')}.json"
        (self.dir / name).write_text(json.dumps({"op": op, "args": args, "key": key, "queued_at": time.time()}, default=str))
        logger.warning(f"CRM unavailable: queued {op} ({key}) in the outbox ({len(self)} waiting)")

    def resolved(self) -> dict[str, str]:
        try:
            return json.loads(self._resolved_path.read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def remember(self, key: str, real_id: str) -> None:
        m = self.resolved()
        m[key] = real_id
        self._resolved_path.write_text(json.dumps(m))

    def bury(self, path: Path, reason: str) -> None:
        self.dead.mkdir(exist_ok=True)
        data = json.loads(path.read_text())
        data["dead_reason"] = reason
        (self.dead / path.name).write_text(json.dumps(data, default=str))
        path.unlink()


class ResilientCRM:
    """Wraps any CRMClient. Same interface; writes survive an outage."""

    def __init__(self, inner: CRMClient, outbox: Outbox):
        self.inner = inner
        self.outbox = outbox
        self._lock = asyncio.Lock()

    # -- reads pass through ------------------------------------------------------------------------
    async def get_config(self) -> AgentConfig:
        return await self.inner.get_config()

    async def lookup_contact(self, phone: str) -> ContactMatch | None:
        return await self.inner.lookup_contact(phone)

    async def list_open_enquiries(self, contact_id: str) -> list[OpenEnquiry]:
        return await self.inner.list_open_enquiries(contact_id)

    async def list_destinations(self) -> list[Destination]:
        return await self.inner.list_destinations()

    async def get_enquiry(self, enquiry_id: str) -> Payload:
        return await self.inner.get_enquiry(enquiry_id)

    # -- writes ------------------------------------------------------------------------------------
    def _unresolved(self, args: dict[str, Any]) -> bool:
        resolved = self.outbox.resolved()
        found: list[bool] = []

        def check(v):
            if isinstance(v, str) and v.startswith(QUEUED) and v[len(QUEUED):] not in resolved:
                found.append(True)
            return v

        _walk(args, check)
        return bool(found)

    def _resolve(self, args: dict[str, Any]) -> dict[str, Any]:
        resolved = self.outbox.resolved()
        return _walk(args, lambda v: resolved.get(v[len(QUEUED):], v) if isinstance(v, str) and v.startswith(QUEUED) else v)

    async def _write(self, op: str, args: dict[str, Any], key: str, provisional):
        async with self._lock:
            if len(self.outbox):
                await self._flush_locked()
            if len(self.outbox) or self._unresolved(args):
                self.outbox.put(op, args, key)
                return provisional
            try:
                return await getattr(self.inner, op)(**self._resolve(args), idempotency_key=key)
            except CRMUnavailable:
                self.outbox.put(op, args, key)
                return provisional

    async def create_enquiry(self, lead: Payload, *, idempotency_key: str) -> EnquiryRef:
        return await self._write("create_enquiry", {"lead": lead}, idempotency_key,
                                 EnquiryRef(QUEUED + idempotency_key, "", lead.get("stage", "new"), True, queued=True))

    async def update_enquiry(self, enquiry_id: str, patch: Payload, *, idempotency_key: str) -> EnquiryRef:
        return await self._write("update_enquiry", {"enquiry_id": enquiry_id, "patch": patch}, idempotency_key,
                                 EnquiryRef(enquiry_id, "", patch.get("stage", "new"), False, queued=True))

    async def add_note(self, enquiry_id: str, text: str, *, idempotency_key: str) -> None:
        await self._write("add_note", {"enquiry_id": enquiry_id, "text": text}, idempotency_key, None)

    async def add_task(self, task: Payload, *, idempotency_key: str) -> TaskRef:
        return await self._write("add_task", {"task": task}, idempotency_key, TaskRef(QUEUED + idempotency_key, None, queued=True))

    async def log_call(self, call: Payload, *, idempotency_key: str) -> CallRef:
        return await self._write("log_call", {"call": call}, idempotency_key, CallRef(QUEUED + idempotency_key, queued=True))

    # -- replay ------------------------------------------------------------------------------------
    async def flush(self) -> int:
        async with self._lock:
            return await self._flush_locked()

    async def _flush_locked(self) -> int:
        sent = 0
        for path in self.outbox.entries():
            entry = json.loads(path.read_text())
            try:
                result = await getattr(self.inner, entry["op"])(**self._resolve(entry["args"]), idempotency_key=entry["key"])
            except CRMUnavailable:
                break  # still down; keep order, try again later
            except CRMError as e:
                logger.error(f"outbox entry {path.name} rejected by the CRM, moved to dead/: {e}")
                self.outbox.bury(path, str(e))
                continue
            rid = getattr(result, "id", None)
            if rid:
                self.outbox.remember(entry["key"], rid)
            path.unlink()
            sent += 1
            logger.info(f"outbox: replayed {entry['op']} ({entry['key']})")
        return sent

    async def run_forever(self, interval_s: float = 30.0) -> None:
        while True:
            await asyncio.sleep(interval_s)
            if len(self.outbox):
                try:
                    await self.flush()
                except Exception as e:  # never let the retry worker die
                    logger.error(f"outbox flush failed: {e}")


__all__ = ["QUEUED", "Outbox", "ResilientCRM"]
