"""Whole-call behaviour through the real tools and session, with the CRM mocked. No audio, no LLM."""

import pytest

from agent.crm.models import ContactMatch, OpenEnquiry
from agent.tools.lead_tools import LeadTools, call_tool

CALLER = "+919000000001"


async def run(tools: LeadTools, tool: str, **args):
    return await call_tool(tools, tool, args)


async def fill_everything(tools: LeadTools, *, name="Priya Sharma"):
    await run(tools, "set_lead_field", name="destination", value="the backwaters")
    await run(tools, "set_lead_field", name="travel_dates", value="12th to 19th December")
    await run(tools, "set_lead_field", name="adults", value="two")
    await run(tools, "set_lead_field", name="children", value="none")
    await run(tools, "set_lead_field", name="trip_type", value="me and my wife")
    await run(tools, "set_lead_field", name="budget", value="one and a half lakh")
    await run(tools, "set_lead_field", name="departure_city", value="from pune")
    await run(tools, "set_lead_field", name="full_name", value=f"my name is {name.lower()}")


def convo(*lines: tuple[str, str]):
    return [{"role": "system", "content": "x"}, *({"role": r, "content": t} for r, t in lines)]


async def test_happy_path_creates_one_qualified_lead(make_session, mock):
    s = make_session(phone=CALLER)
    t = LeadTools(s)
    r = await run(t, "identify_trip", description="we want to see the backwaters")
    assert r["matched"] and r["candidates"][0]["destination"] == "Kerala"
    await fill_everything(t)

    done = await run(t, "get_missing_fields")  # the caller-ID number is already on the form
    assert done["all_collected"] and "Kerala" in done["read_back"] and "1.5 lakh" in done["read_back"]
    assert "nine zero zero zero zero zero zero zero zero one" in done["read_back"]
    out = await run(t, "submit_lead", caller_confirmed=True)
    assert out["ok"] and out["reference_spoken"].startswith("W T")

    (lead,) = mock.enquiries.values()
    assert lead["destination"] == "kerala" and lead["budget_total_inr"] == 150000 and lead["children"] == 0
    assert lead["stage"] == "qualified" and lead["phone"] == CALLER and lead["trip_type"] == "couple"
    assert lead["travel_start"].endswith("12-12") and lead["nights"] == 7


async def test_submit_needs_everything_readback_and_a_yes(make_session, mock):
    s = make_session(phone=CALLER)
    t = LeadTools(s)
    assert (await run(t, "submit_lead", caller_confirmed=True))["ok"] is False  # nothing collected
    await fill_everything(t)
    await run(t, "set_lead_field", name="phone", value="yes")
    assert "read the summary back" in (await run(t, "submit_lead", caller_confirmed=True))["error"]
    await run(t, "get_missing_fields")
    assert (await run(t, "submit_lead", caller_confirmed=False))["ok"] is False
    assert not mock.enquiries
    # changing a detail after the read-back forces another read-back
    await run(t, "set_lead_field", name="budget", value="2 lakh")
    assert (await run(t, "submit_lead", caller_confirmed=True))["ok"] is False
    await run(t, "get_missing_fields")
    assert (await run(t, "submit_lead", caller_confirmed=True))["ok"] is True
    assert next(iter(mock.enquiries.values()))["budget_total_inr"] == 200000


async def test_double_submit_does_not_duplicate(make_session, mock):
    s = make_session(phone=CALLER)
    t = LeadTools(s)
    await fill_everything(t)
    await run(t, "set_lead_field", name="phone", value="yes")
    await run(t, "get_missing_fields")
    a = await run(t, "submit_lead", caller_confirmed=True)
    b = await run(t, "submit_lead", caller_confirmed=True)
    assert a["ok"] and b["ok"]
    assert len(mock.enquiries) == 1


async def test_bad_values_are_rejected_with_a_reason(make_session):
    t = LeadTools(make_session(phone=CALLER))
    assert not (await run(t, "set_lead_field", name="budget", value="50"))["ok"]
    assert not (await run(t, "set_lead_field", name="destination", value="Mars"))["ok"]
    assert not (await run(t, "set_lead_field", name="phone", value="12345"))["ok"]
    assert not (await run(t, "set_lead_field", name="shoe_size", value="9"))["ok"]
    r = await run(t, "set_lead_field", name="travel_dates", value="3rd March 2020")
    assert not r["ok"] and "past" in r["message"]


async def test_per_person_budget_needs_headcount_first(make_session):
    s = make_session(phone=CALLER)
    t = LeadTools(s)
    r = await run(t, "set_lead_field", name="budget", value="fifty thousand per person")
    assert not r["ok"] and "how many people" in r["message"]
    await run(t, "set_lead_field", name="adults", value="two")
    await run(t, "set_lead_field", name="children", value="1")
    r = await run(t, "set_lead_field", name="budget", value="fifty thousand per person")
    assert r["ok"] and s.form.values["budget_total_inr"] == 150000


async def test_a_corrected_number_replaces_caller_id_and_needs_a_new_readback(make_session, mock):
    s = make_session(phone=CALLER)
    t = LeadTools(s)
    await fill_everything(t)
    await run(t, "get_missing_fields")
    await run(t, "set_lead_field", name="phone", value="nine eight seven six five four three two one zero")
    assert (await run(t, "submit_lead", caller_confirmed=True))["ok"] is False
    await run(t, "get_missing_fields")
    assert (await run(t, "submit_lead", caller_confirmed=True))["ok"] is True
    assert next(iter(mock.enquiries.values()))["phone"] == "+919876543210"


async def test_no_caller_id_means_the_number_must_be_asked(make_session):
    t = LeadTools(make_session())
    await fill_everything(t)
    miss = await run(t, "get_missing_fields")
    assert miss["all_collected"] is False and "phone number" in miss["next_to_ask"]


async def test_wrong_tool_arguments_do_not_crash(make_session):
    t = LeadTools(make_session())
    assert (await call_tool(t, "set_lead_field", {"value": "x"}))["ok"] is False
    assert (await call_tool(t, "nope", {}))["ok"] is False
    r = await call_tool(t, "identify_trip", {"description": "kerala", "junk": 1})
    assert r["matched"]


async def test_returning_caller_gets_asked_before_a_second_enquiry(make_session, mock):
    cid = mock.seed_contact("Priya Sharma", CALLER, returning=True, enquiry={"destination": "kerala", "stage": "contacted"})
    contact = await mock.lookup_contact(CALLER)
    s = make_session(phone=CALLER, contact=contact)
    t = LeadTools(s)
    assert cid and s.form.values["full_name"] == "Priya Sharma"
    await fill_everything(t)
    await run(t, "set_lead_field", name="phone", value="yes")
    await run(t, "get_missing_fields")
    ask = await run(t, "submit_lead", caller_confirmed=True)
    assert ask["ok"] is False and ask["needs_decision"] and "add these details" in ask["error"]
    assert len(mock.enquiries) == 1
    done = await run(t, "submit_lead", caller_confirmed=True, update_existing=True)
    assert done["ok"] and len(mock.enquiries) == 1
    (e,) = mock.enquiries.values()
    assert e["stage"] == "qualified" and e["budget_total_inr"] == 150000


async def test_returning_caller_new_destination_is_not_a_duplicate(make_session, mock):
    mock.seed_contact("Priya Sharma", CALLER, enquiry={"destination": "goa"})
    s = make_session(phone=CALLER, contact=await mock.lookup_contact(CALLER))
    t = LeadTools(s)
    await fill_everything(t)  # kerala
    await run(t, "set_lead_field", name="phone", value="yes")
    await run(t, "get_missing_fields")
    assert (await run(t, "submit_lead", caller_confirmed=True))["ok"]
    assert len(mock.enquiries) == 2


async def test_handoff_creates_urgent_task_once(make_session, mock):
    s = make_session(phone=CALLER)
    t = LeadTools(s)
    r = await run(t, "handoff_to_human", reason="wants to cancel the booking and get a refund")
    assert r["ok"] and "Never" not in r["say"] or "refund" in r["say"]
    await run(t, "handoff_to_human", reason="wants to cancel the booking and get a refund")
    (task,) = mock.tasks
    assert task["kind"] == "urgent_handoff" and task["contact_phone"] == CALLER and "needs-human" in task["add_tags"]
    assert task["reason"] == "cancellation"


async def test_handoff_without_any_phone_asks_for_one_then_finalize_writes_it(make_session, mock):
    s = make_session()  # no caller ID
    t = LeadTools(s)
    r = await run(t, "handoff_to_human", reason="medical emergency")
    assert r["pending"] and not mock.tasks
    await run(t, "set_lead_field", name="phone", value="98765 43210")
    await s.finalize(convo(("user", "my father is in hospital, a medical emergency")), duration_s=30)
    assert [x["kind"] for x in mock.tasks] == ["urgent_handoff"]


async def test_safety_net_hands_off_even_if_the_model_forgot(make_session, mock):
    s = make_session(phone=CALLER)
    res = await s.finalize(convo(("assistant", "Hello"), ("user", "I want to cancel my trip and need a refund")), duration_s=40)
    assert [t["kind"] for t in mock.tasks] == ["urgent_handoff"]
    assert res["outcome"] == "handoff"
    assert mock.calls[0]["sentiment"] == "negative"


async def test_hangup_midway_saves_an_incomplete_lead_and_a_callback(make_session, mock):
    s = make_session(phone=CALLER)
    t = LeadTools(s)
    await run(t, "set_lead_field", name="destination", value="Munnar")
    await run(t, "set_lead_field", name="adults", value="3")
    res = await s.finalize(convo(("assistant", "Hi"), ("user", "munnar for three of us")), duration_s=50)
    (lead,) = mock.enquiries.values()
    assert "incomplete-voice-lead" in lead["tags"] and lead["stage"] == "new" and lead["destination"] == "kerala"
    assert [x["kind"] for x in mock.tasks] == ["callback"]
    assert res["outcome"] == "callback"
    assert mock.calls[0]["enquiry_id"] == lead["id"]


async def test_chat_without_a_lead_is_just_info(make_session, mock):
    s = make_session(phone=CALLER)
    res = await s.finalize(convo(("assistant", "Hi"), ("user", "what is the weather like in goa in june")), duration_s=20)
    assert res["outcome"] == "info" and not mock.enquiries and not mock.tasks
    assert "enquiry_id" not in mock.calls[0]  # an unlinked lead


async def test_silence_is_no_answer(make_session, mock):
    res = await make_session(phone=CALLER).finalize(convo(("assistant", "Hi")), duration_s=8)
    assert res["outcome"] == "no_answer" and mock.calls[0]["status"] == "missed"


async def test_declined(make_session, mock):
    res = await make_session(phone=CALLER).finalize(convo(("user", "no thanks, I am not interested")), duration_s=8)
    assert res["outcome"] == "not_interested"


async def test_transcript_masks_passports_and_cards(make_session, mock):
    s = make_session(phone=CALLER)
    await s.finalize(convo(("user", "my passport is N1234567 and card 4111 1111 1111 1111")), duration_s=20)
    text = " ".join(x["text"] for x in mock.calls[0]["transcript"])
    assert "N1234567" not in text and "4111" not in text.replace("••••1111", "") and "••••4567" in text


async def test_callback_tool_parses_time_and_links_phone(make_session, mock):
    s = make_session(phone=CALLER)
    t = LeadTools(s)
    r = await run(t, "schedule_callback", when="tomorrow evening", reason="wants to talk to a person")
    assert r["ok"] and "6 p m" in r["when_spoken"]
    (task,) = mock.tasks
    assert task["kind"] == "callback" and task["contact_phone"] == CALLER and task["due_at"].endswith("+05:30")


async def test_callback_without_any_contact_asks_for_phone(make_session):
    r = await run(LeadTools(make_session()), "schedule_callback", when="tomorrow")
    assert r["ok"] is False and "phone" in r["error"]


async def test_tags_follow_the_conversation(make_session, mock):
    s = make_session(phone=CALLER)
    t = LeadTools(s)
    await fill_everything(t)
    await run(t, "set_lead_field", name="trip_type", value="honeymoon")
    await run(t, "set_lead_field", name="phone", value="yes")
    await run(t, "get_missing_fields")
    s.messages_provider = lambda: convo(("user", "can you give me a discount?"))
    await run(t, "submit_lead", caller_confirmed=True)
    (lead,) = mock.enquiries.values()
    assert {"honeymoon", "discount-asked", "voice-lead"} <= set(lead["tags"])


async def test_crm_outage_mid_call_queues_everything_and_replays(make_session, mock, resilient):
    s = make_session(crm=resilient, phone=CALLER)
    t = LeadTools(s)
    await fill_everything(t)
    await run(t, "set_lead_field", name="phone", value="yes")
    await run(t, "get_missing_fields")
    mock.down = True
    out = await run(t, "submit_lead", caller_confirmed=True)
    assert out["ok"] and out["reference_spoken"] is None and "slow" in out["say"]
    await s.finalize(convo(("user", "ok thanks")), duration_s=90)
    assert not mock.enquiries and len(resilient.outbox) >= 2  # lead + call log (+ note)
    mock.down = False
    await resilient.flush()
    assert len(resilient.outbox) == 0 and len(mock.enquiries) == 1
    (lead,) = mock.enquiries.values()
    assert mock.calls[0]["enquiry_id"] == lead["id"] and mock.notes and mock.notes[0][0] == lead["id"]


async def test_crm_down_on_plain_mock_gives_a_callback_message(make_session, mock):
    s = make_session(phone=CALLER)
    t = LeadTools(s)
    await fill_everything(t)
    await run(t, "set_lead_field", name="phone", value="yes")
    await run(t, "get_missing_fields")
    mock.down = True
    out = await run(t, "submit_lead", caller_confirmed=True)
    assert out["ok"] is False and out["crm_down"] and "call them back" in out["error"]


async def test_crm_validation_error_clears_the_field_to_reask(make_session, mock):
    s = make_session(phone=CALLER)
    t = LeadTools(s)
    await fill_everything(t)
    await run(t, "set_lead_field", name="phone", value="yes")
    await run(t, "get_missing_fields")
    s.form.values["adults"] = 0  # sneak a bad value past the form to simulate a CRM-side rule
    out = await run(t, "submit_lead", caller_confirmed=True)
    assert out["ok"] is False and out["reask"] == ["adults"]
    assert "adults" not in s.form.values
    assert any(m[0] == "adults" for m in s.form.missing())


@pytest.mark.parametrize("ref,spoken", [("WT-1058", "W T ten fifty eight"), ("WT-1106", "W T eleven oh 6"), ("WT-1100", "W T eleven hundred")])
def test_reference_is_spoken_naturally(ref, spoken):
    from agent.call.session import _spoken_ref

    assert _spoken_ref(ref) == spoken


def test_open_enquiry_models():
    c = ContactMatch("trv_1", "A B", CALLER, False, [OpenEnquiry("e1", "WT-1", "goa", "new")])
    assert c.open_enquiries[0].reference == "WT-1"


async def test_guard_starts_the_handoff_immediately_and_constrains_the_reply(make_session, mock):
    from agent.call.guard import apply_guard

    s = make_session(phone=CALLER)

    class Ctx:
        def __init__(self):
            self.messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "I want to cancel and get a refund"}]

        def add_message(self, m):
            self.messages.append(m)

    ctx = Ctx()
    upto = apply_guard(s, ctx, 0)
    assert ctx.messages[-1]["role"] == "developer" and "Do not discuss refunds" in ctx.messages[-1]["content"]
    await s.drain()
    assert [t["kind"] for t in mock.tasks] == ["urgent_handoff"]
    # the same message is never guarded twice (a tool round-trip re-runs the LLM on the same context)
    assert apply_guard(s, ctx, upto) == upto and len(ctx.messages) == 3
    ctx.messages.append({"role": "user", "content": "and I want the refund now"})
    apply_guard(s, ctx, upto)
    await s.drain()
    assert len(mock.tasks) == 1  # still a single task


async def test_guard_ignores_ordinary_talk(make_session, mock):
    s = make_session(phone=CALLER)
    assert s.guard_user_text("we want a beach holiday in Goa") is None
    assert not mock.tasks


async def test_partial_details_never_patch_an_unrelated_open_enquiry(make_session, mock):
    mock.seed_contact("Priya Sharma", CALLER, enquiry={"destination": "kerala", "stage": "contacted"})
    s = make_session(phone=CALLER, contact=await mock.lookup_contact(CALLER))
    await run(LeadTools(s), "set_lead_field", name="adults", value="5")  # no destination yet
    await s.finalize(convo(("user", "for five people")), duration_s=10)
    kerala = next(e for e in mock.enquiries.values() if e["destination"] == "kerala" and e["stage"] == "contacted")
    assert "adults" not in kerala
    assert any("incomplete-voice-lead" in e.get("tags", []) for e in mock.enquiries.values())


async def test_exact_destination_name_is_set_without_a_confirmation_round_trip(make_session):
    s = make_session(phone=CALLER)
    r = await run(LeadTools(s), "identify_trip", description="I want to go to Goa")
    assert r["destination_set"] == "Goa" and s.form.values["destination"] == "goa"
    r = await run(LeadTools(s), "identify_trip", description="the backwaters")
    assert "destination_set" not in r and r["candidates"][0]["destination"] == "Kerala"  # an alias still needs a yes


async def test_safety_net_books_a_callback_the_model_only_talked_about(make_session, mock):
    s = make_session(phone=CALLER)
    res = await s.finalize(convo(("assistant", "Hi"), ("user", "can someone call me back tomorrow evening"), ("assistant", "Sure, why?")), duration_s=30)
    (task,) = mock.tasks
    assert task["kind"] == "callback" and task["contact_phone"] == CALLER and task["due_at"]
    assert res["outcome"] == "callback"


async def test_no_duplicate_callback_when_the_tool_already_ran(make_session, mock):
    s = make_session(phone=CALLER)
    await run(LeadTools(s), "schedule_callback", when="tomorrow evening", reason="info")
    await s.finalize(convo(("user", "please call me back tomorrow evening")), duration_s=30)
    assert len(mock.tasks) == 1


@pytest.mark.parametrize("text", ["call me tomorrow", "can someone ring me", "I want to speak to a person", "give me a call", "have someone call me", "call back later"])
def test_callback_phrases(text):
    from agent.call.analysis import CALLBACK_ASK

    assert CALLBACK_ASK.search(text)


@pytest.mark.parametrize("text", ["I will call you", "what is the call time of the flight", "we are a call centre"])
def test_not_callback_phrases(text):
    from agent.call.analysis import CALLBACK_ASK

    assert not CALLBACK_ASK.search(text)


def test_a_phone_number_stored_as_the_name_is_not_a_name():
    assert ContactMatch("t", "+919000000002", CALLER).known_name is None
    assert ContactMatch("t", "Priya Sharma", CALLER).known_name == "Priya Sharma"
    assert ContactMatch("t", "", CALLER).known_name is None


async def test_unnamed_contact_is_not_greeted_by_number(make_session, mock):
    from agent.prompts.base import greeting_instruction, system_prompt

    contact = ContactMatch("t", "+919000000002", CALLER, False, [])
    s = make_session(phone=CALLER, contact=contact)
    assert "full_name" not in s.form.values
    assert "+9190" not in greeting_instruction(s.config, s)
    assert "name not known yet" in system_prompt(s.config, s)
