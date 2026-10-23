"""CRM adapter for the ascend-travel-crm REST API (`/api/agent/v1`, see docs/crm-api.openapi.yaml)."""

import asyncio
from typing import Any

import httpx
from loguru import logger

from agent.crm.models import (
    DEFAULT_ESCALATE_ON,
    AgentConfig,
    CallRef,
    ContactMatch,
    CRMError,
    CRMNotFound,
    CRMUnavailable,
    CRMValidationError,
    Destination,
    EnquiryRef,
    FieldError,
    OpenEnquiry,
    Payload,
    TaskRef,
)

READ_TIMEOUT_S = 2.5  # the agent waits on reads mid-sentence (docs/07 section 6: p95 < 300 ms)
WRITE_TIMEOUT_S = 8.0


class HttpCRM:
    def __init__(self, base_url: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None):
        if not base_url:
            raise CRMError("CRM_BASE_URL is not set")
        if not api_key:
            raise CRMError("CRM_API_KEY is not set")
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "User-Agent": "local-voice-agent/0.1"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- transport -------------------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        timeout: float = READ_TIMEOUT_S,
        attempts: int = 1,
    ) -> Any:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                r = await self._http.request(method, path, json=json, params=params, headers=headers, timeout=timeout)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                last = e
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.3)
                    continue
                raise CRMUnavailable(f"{method} {path}: {type(e).__name__}") from e
            if r.status_code in (429, 502, 503, 504, 500, 409):
                # 409 = same Idempotency-Key still processing; 503 = agent API switched off or key unset
                last = CRMUnavailable(f"{method} {path}: HTTP {r.status_code}")
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.3)
                    continue
                raise last
            return self._unwrap(method, path, r)
        raise CRMUnavailable(str(last))

    @staticmethod
    def _unwrap(method: str, path: str, r: httpx.Response) -> Any:
        if r.status_code in (401, 403):
            raise CRMError(f"{method} {path}: the CRM rejected our API key (HTTP {r.status_code}); check CRM_API_KEY")
        if r.status_code == 404:
            raise CRMNotFound(f"{method} {path}")
        if r.status_code == 422:
            try:
                errs = [FieldError(e.get("field", "body"), e.get("message", "invalid")) for e in r.json().get("errors", [])]
            except ValueError:
                errs = [FieldError("body", "invalid")]
            raise CRMValidationError(errs or [FieldError("body", "invalid")])
        if r.status_code >= 400:
            raise CRMError(f"{method} {path}: HTTP {r.status_code} {r.text[:200]}")
        if not r.content:
            return None
        return r.json()

    # -- reads -------------------------------------------------------------------------------------
    async def get_config(self) -> AgentConfig:
        j = await self._request("GET", "/config")
        company = j.get("company") or {}
        return AgentConfig(
            enabled=bool(j.get("enabled", True)),
            name=j.get("name") or "Aanya",
            greeting=j.get("greeting"),
            escalate_on=j.get("escalate_on") or list(DEFAULT_ESCALATE_ON),
            max_call_minutes=int(j.get("max_call_minutes") or 8),
            company_name=company.get("name"),
            company_phone=company.get("phone"),
            tone=j.get("tone"),
        )

    async def lookup_contact(self, phone: str) -> ContactMatch | None:
        try:
            j = await self._request("GET", "/contacts/lookup", params={"phone": phone})
        except CRMNotFound:
            return None
        return ContactMatch(
            contact_id=j["contact_id"],
            full_name=j.get("full_name", ""),
            phone=j.get("phone", phone),
            returning=bool(j.get("returning")),
            open_enquiries=[
                OpenEnquiry(
                    id=e["id"],
                    reference=e["reference"],
                    destination=e.get("destination", ""),
                    stage=e.get("stage", ""),
                    owner_first_name=e.get("owner_first_name"),
                    updated_at=e.get("updated_at"),
                    travel_month=e.get("travel_month"),
                )
                for e in j.get("open_enquiries", [])
            ],
        )

    async def list_open_enquiries(self, contact_id: str) -> list[OpenEnquiry]:
        # The API returns open enquiries with the contact; callers use lookup_contact() for them.
        return []

    async def list_destinations(self) -> list[Destination]:
        j = await self._request("GET", "/destinations")
        return [Destination(d["code"], d["name"], d.get("aliases", []), d.get("from_price_inr")) for d in j]

    async def get_enquiry(self, enquiry_id: str) -> Payload:
        return await self._request("GET", f"/enquiries/{enquiry_id}")

    # -- writes -----------------------------------------------------------------------------------
    async def create_enquiry(self, lead: Payload, *, idempotency_key: str) -> EnquiryRef:
        j = await self._request("POST", "/enquiries", json=lead, idempotency_key=idempotency_key, timeout=WRITE_TIMEOUT_S, attempts=2)
        return EnquiryRef(j["id"], j["reference"], j["stage"], bool(j.get("created", True)))

    async def update_enquiry(self, enquiry_id: str, patch: Payload, *, idempotency_key: str) -> EnquiryRef:
        j = await self._request("PATCH", f"/enquiries/{enquiry_id}", json=patch, idempotency_key=idempotency_key, timeout=WRITE_TIMEOUT_S, attempts=2)
        return EnquiryRef(j["id"], j["reference"], j["stage"], bool(j.get("created", False)))

    async def add_note(self, enquiry_id: str, text: str, *, idempotency_key: str) -> None:
        await self._request("POST", f"/enquiries/{enquiry_id}/notes", json={"text": text}, idempotency_key=idempotency_key, timeout=WRITE_TIMEOUT_S, attempts=2)

    async def add_task(self, task: Payload, *, idempotency_key: str) -> TaskRef:
        j = await self._request("POST", "/tasks", json=task, idempotency_key=idempotency_key, timeout=WRITE_TIMEOUT_S, attempts=2)
        return TaskRef(j["id"], j.get("assigned_to"))

    async def log_call(self, call: Payload, *, idempotency_key: str) -> CallRef:
        j = await self._request("POST", "/calls", json=call, idempotency_key=idempotency_key, timeout=WRITE_TIMEOUT_S, attempts=2)
        logger.info(f"call logged in CRM as {j['id']}")
        return CallRef(j["id"])
