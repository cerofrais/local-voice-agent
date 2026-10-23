"""The same tests run against MockCRM and the live CRM (HttpCRM), so the mock cannot drift from the real API."""

import uuid

import httpx
import pytest
from tests.conftest import fake_phone

from agent.crm import HttpCRM
from agent.crm.models import CRMError, CRMNotFound, CRMUnavailable, CRMValidationError

LEAD = {
    "full_name": "Contract Test",
    "destination": "kerala",
    "adults": 2,
    "children": 1,
    "trip_type": "family",
    "budget_total_inr": 150000,
    "departure_city": "Pune",
    "month_if_flexible": "2026-12",
    "nights": 6,
    "tags": ["voice-lead"],
}


def key(label: str) -> str:
    return f"pytest-{uuid.uuid4().hex[:8]}:{label}"


async def test_config_and_destinations(crm):
    cfg = await crm.get_config()
    assert cfg.name and cfg.escalate_on
    dests = {d.code: d for d in await crm.list_destinations()}
    assert len(dests) == 12
    assert "backwaters" in dests["kerala"].aliases


async def test_lookup_miss_and_hit(crm):
    phone = fake_phone()
    assert await crm.lookup_contact(phone) is None
    ref = await crm.create_enquiry({**LEAD, "phone": phone}, idempotency_key=key("create"))
    # any common phone format finds the same traveller
    local = "0" + phone[-10:]
    hit = await crm.lookup_contact(local[:6] + " " + local[6:])
    assert hit and hit.full_name == "Contract Test"
    assert hit.contact_id
    assert any(e.id == ref.id for e in hit.open_enquiries)


async def test_create_is_idempotent(crm):
    phone, k = fake_phone(), key("create")
    a = await crm.create_enquiry({**LEAD, "phone": phone}, idempotency_key=k)
    b = await crm.create_enquiry({**LEAD, "phone": phone}, idempotency_key=k)
    assert a.id == b.id and a.reference == b.reference
    assert a.created is True and b.created is False
    c = await crm.lookup_contact(phone)
    assert len(c.open_enquiries) == 1


async def test_update_and_read_back(crm):
    phone = fake_phone()
    ref = await crm.create_enquiry({**LEAD, "phone": phone}, idempotency_key=key("create"))
    await crm.update_enquiry(ref.id, {"nights": 7, "add_tags": ["flexible-dates"]}, idempotency_key=key("patch"))
    e = await crm.get_enquiry(ref.id)
    assert e["nights"] == 7
    assert "flexible-dates" in e["tags"]


async def test_validation_errors_are_field_level(crm):
    with pytest.raises(CRMValidationError) as exc:
        await crm.create_enquiry({**LEAD, "phone": "123"}, idempotency_key=key("bad"))
    assert [e.field for e in exc.value.errors] == ["phone"]
    assert "phone" in exc.value.spoken().lower()
    with pytest.raises(CRMValidationError) as exc:
        await crm.create_enquiry({"phone": fake_phone(), "full_name": "A B"}, idempotency_key=key("missing"))
    assert {"destination", "adults"} <= {e.field for e in exc.value.errors}


async def test_agent_cannot_advance_stage(crm):
    ref = await crm.create_enquiry({**LEAD, "phone": fake_phone()}, idempotency_key=key("create"))
    with pytest.raises(CRMValidationError):
        await crm.update_enquiry(ref.id, {"stage": "advance_paid"}, idempotency_key=key("stage"))


async def test_incomplete_lead_needs_only_a_phone(crm):
    phone = fake_phone()
    ref = await crm.create_enquiry({"phone": phone, "destination": "goa", "tags": ["voice-lead", "incomplete-voice-lead"], "stage": "new"}, idempotency_key=key("partial"))
    assert ref.reference
    e = await crm.get_enquiry(ref.id)
    assert "incomplete-voice-lead" in e["tags"]


async def test_tasks_notes_and_calls(crm):
    phone = fake_phone()
    ref = await crm.create_enquiry({**LEAD, "phone": phone}, idempotency_key=key("create"))
    t = await crm.add_task({"kind": "urgent_handoff", "enquiry_id": ref.id, "reason": "cancellation", "summary": "wants to cancel", "add_tags": ["needs-human"]}, idempotency_key=key("task"))
    assert t.id
    assert "needs-human" in (await crm.get_enquiry(ref.id))["tags"]
    await crm.add_task({"kind": "callback", "contact_phone": phone, "reason": "info", "due_at": "2030-01-01T11:00:00+05:30"}, idempotency_key=key("cb"))
    await crm.add_note(ref.id, "Prefers evening calls", idempotency_key=key("note"))
    c = await crm.log_call(
        {
            "direction": "inbound",
            "enquiry_id": ref.id,
            "caller_phone": phone,
            "started_at": "2026-10-23T10:00:00+05:30",
            "duration_s": 60,
            "outcome": "qualified",
            "transcript": [{"speaker": "caller", "text": "hello"}],
        },
        idempotency_key=key("call"),
    )
    assert c.id
    with pytest.raises(CRMValidationError):
        await crm.add_task({"kind": "callback", "reason": "x"}, idempotency_key=key("badtask"))


async def test_unknown_enquiry_is_not_found(crm):
    with pytest.raises(CRMNotFound):
        await crm.get_enquiry("enq_does_not_exist")


# -- HttpCRM transport behaviour (no live CRM needed) ---------------------------------------------
def _http(handler):
    return HttpCRM("http://crm.test/api/agent/v1", "k", transport=httpx.MockTransport(handler))


async def test_http_timeout_and_5xx_are_unavailable():
    def boom(_req):
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(CRMUnavailable):
        await _http(boom).lookup_contact("+919876543210")
    for status in (500, 503, 429, 409):
        with pytest.raises(CRMUnavailable):
            await _http(lambda _r, s=status: httpx.Response(s)).get_config()


async def test_http_bad_key_is_a_clear_error_not_unavailable():
    with pytest.raises(CRMError) as exc:
        await _http(lambda _r: httpx.Response(401)).get_config()
    assert not isinstance(exc.value, CRMUnavailable)
    assert "CRM_API_KEY" in str(exc.value)


async def test_http_sends_bearer_and_idempotency_key():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(req.headers)
        return httpx.Response(201, json={"id": "enq_1", "reference": "WT-1", "stage": "new", "created": True})

    await _http(handler).create_enquiry({"phone": "+919876543210"}, idempotency_key="call1:create")
    assert seen["authorization"] == "Bearer k"
    assert seen["idempotency-key"] == "call1:create"
