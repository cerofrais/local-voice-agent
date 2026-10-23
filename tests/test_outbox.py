import json

from tests.conftest import fake_phone

from agent.crm.models import CRMUnavailable

LEAD = {"full_name": "Out Box", "destination": "goa", "adults": 2, "trip_type": "couple", "budget_total_inr": 90000, "departure_city": "Pune"}


async def test_writes_queue_while_down_and_replay_in_order(resilient, mock):
    phone = fake_phone()
    mock.down = True
    ref = await resilient.create_enquiry({**LEAD, "phone": phone}, idempotency_key="c1:create_enquiry")
    assert ref.queued and ref.id.startswith("queued:")
    # a call log that points at the queued lead must wait for it
    await resilient.log_call(
        {"direction": "inbound", "enquiry_id": ref.id, "started_at": "2026-10-23T10:00:00+05:30", "duration_s": 5, "outcome": "qualified"},
        idempotency_key="c1:log_call",
    )
    assert len(resilient.outbox) == 2
    mock.down = False
    assert await resilient.flush() == 2
    assert len(resilient.outbox) == 0
    (real_id,) = mock.enquiries
    assert mock.calls[0]["enquiry_id"] == real_id  # placeholder replaced by the real id


async def test_replay_never_duplicates(resilient, mock):
    phone = fake_phone()
    mock.down = True
    await resilient.create_enquiry({**LEAD, "phone": phone}, idempotency_key="c2:create_enquiry")
    mock.down = False
    await resilient.flush()
    again = await resilient.create_enquiry({**LEAD, "phone": phone}, idempotency_key="c2:create_enquiry")
    assert again.created is False
    assert len(mock.enquiries) == 1


async def test_new_writes_wait_behind_queued_ones(resilient, mock):
    mock.down = True
    await resilient.add_task({"kind": "callback", "contact_phone": fake_phone(), "reason": "a"}, idempotency_key="c3:a")
    mock.down = False
    # CRM is back, but order must hold: this write triggers a flush first, then goes straight through
    await resilient.add_task({"kind": "callback", "contact_phone": fake_phone(), "reason": "b"}, idempotency_key="c3:b")
    assert [t["reason"] for t in mock.tasks] == ["a", "b"]
    assert len(resilient.outbox) == 0


async def test_rejected_entries_move_to_dead_letter(resilient, mock):
    mock.down = True
    await resilient.create_enquiry({"phone": "123"}, idempotency_key="c4:create_enquiry")
    mock.down = False
    await resilient.flush()
    assert len(resilient.outbox) == 0
    dead = list(resilient.outbox.dead.glob("*.json"))
    assert len(dead) == 1 and "dead_reason" in json.loads(dead[0].read_text())


async def test_reads_raise_when_down(resilient, mock):
    mock.down = True
    try:
        await resilient.lookup_contact("+919876543210")
    except CRMUnavailable:
        return
    raise AssertionError("read should raise")
