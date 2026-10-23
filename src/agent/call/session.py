"""State and CRM side effects for one call.

The LLM drives the conversation through the tools in `agent.tools`; everything those tools (and the
end-of-call wrap-up) write to the CRM goes through this class, so the guardrails live in one place:
read before write, confirm before write, idempotent keys, partial leads saved, hand-off topics always
become an urgent task, PII masked.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from agent.call import analysis
from agent.crm.client import CRMClient
from agent.crm.models import (
    AgentConfig,
    ContactMatch,
    CRMError,
    CRMUnavailable,
    CRMValidationError,
    Destination,
    EnquiryRef,
    OpenEnquiry,
)
from agent.lead import parsers as p
from agent.lead.destinations import DestinationResolver
from agent.lead.form import LeadForm

# CRM field that a 422 on this API field should clear from the form so the agent re-asks it.
API_TO_FORM = {"travel_start": "travel_start", "travel_end": "travel_end", "month_if_flexible": "month_if_flexible"}


@dataclass
class CallSession:
    crm: CRMClient
    config: AgentConfig
    destinations: list[Destination]
    caller_phone: str | None = None
    contact: ContactMatch | None = None
    duplicate_window_days: int = 30
    call_id: str = field(default_factory=lambda: f"{datetime.now(p.IST):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}")
    started_at: datetime = field(default_factory=lambda: datetime.now(p.IST))

    def __post_init__(self) -> None:
        self.resolver = DestinationResolver(self.destinations)
        self.form = LeadForm(self.resolver)
        self.form.prefill(phone=self.caller_phone, full_name=self.contact.known_name if self.contact else None)
        self.submitted: EnquiryRef | None = None
        self.linked_enquiry_id: str | None = None  # the enquiry this call belongs to (new or existing)
        self.callback_ref: str | None = None
        self.handoff_reason: str | None = None
        self.handoff_done = False
        self.discount_asked = False
        self.price_asked = False
        self.crm_trouble = False
        self.events: list[dict[str, Any]] = []  # what we wrote, for the local call log
        self._patch_seq = 0
        self.finalized = False
        self._background: set = set()
        self.messages_provider: Callable[[], list[Any]] | None = None  # set by the bot: the live LLM context

    # -- helpers -----------------------------------------------------------------------------------
    def key(self, action: str) -> str:
        return f"{self.call_id}:{action}"

    def log(self, action: str, **detail: Any) -> None:
        self.events.append({"t": datetime.now(p.IST).isoformat(timespec="seconds"), "action": action, **detail})

    @property
    def contact_phone(self) -> str | None:
        return self.form.values.get("phone") or self.caller_phone

    def open_enquiries(self) -> list[OpenEnquiry]:
        return self.contact.open_enquiries if self.contact else []

    def find_duplicate(self) -> OpenEnquiry | None:
        """Open enquiry for the same destination touched recently: update it instead of creating another."""
        dest = self.form.values.get("destination")
        if not dest:
            return None  # never patch someone's existing enquiry with details for an unknown trip
        cutoff = datetime.now(p.IST) - timedelta(days=self.duplicate_window_days)
        for e in self.open_enquiries():
            if e.destination != dest:
                continue
            try:
                when = datetime.fromisoformat((e.updated_at or "").replace("Z", "+00:00"))
            except ValueError:
                when = cutoff  # unknown age: treat as recent enough to ask
            if when >= cutoff:
                return e
        return None

    def derived_tags(self) -> list[str]:
        v = self.form.values
        tags = []
        if v.get("trip_type") == "honeymoon":
            tags.append("honeymoon")
        if v.get("trip_type") == "parents":
            tags.append("parents")
        if v.get("flexibility") in ("plus_minus_3", "flexible_month") or v.get("month_if_flexible"):
            tags.append("flexible-dates")
        if self.discount_asked:
            tags.append("discount-asked")
        if self.price_asked:
            tags.append("price-asked")
        return tags

    def guard_user_text(self, text: str) -> str | None:
        """Called for every caller turn BEFORE the model answers. If the caller raised an escalation topic,
        start the hand-off now (in the background) and return an instruction for the model's reply.

        This does not rely on the model choosing to call handoff_to_human, and it keeps the spoken reply
        inside the guardrail (never quote refunds or fees)."""
        topic = analysis.detect_escalation(text, self.config.escalate_on)
        if not topic:
            return None
        first = self.handoff_reason is None
        self.handoff_reason = self.handoff_reason or topic
        if first:
            self._bg(self._write_handoff_safe(topic, text))
        return (
            f"The caller just raised {topic.replace('_', ' ')}. Reply in one or two short sentences: say you are sorry, "
            "that you have asked a consultant to call them shortly, and ask if there is anything else. "
            "Do not discuss refunds, fees, cancellation terms or policies, and do not quote any amount."
        )

    def _bg(self, coro) -> None:
        import asyncio

        t = asyncio.ensure_future(coro)
        self._background.add(t)
        t.add_done_callback(self._background.discard)

    async def _write_handoff_safe(self, topic: str, text: str) -> None:
        if not self.contact_phone and not self.linked_enquiry_id:
            return  # no way to reach the caller yet; finalize retries once a number is known
        await self._write_handoff(topic, text)

    async def drain(self) -> None:
        import asyncio

        if self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    def scan_caller_words(self) -> None:
        for m in (self.messages_provider() if self.messages_provider else []):
            if isinstance(m, dict) and m.get("role") == "user" and isinstance(m.get("content"), str):
                self.note_caller_words(m["content"])

    def note_caller_words(self, text: str) -> None:
        """Called for each caller utterance: cheap keyword flags, never decisions."""
        if analysis.DISCOUNT.search(text):
            self.discount_asked = True
        if analysis.PRICE_ASK.search(text):
            self.price_asked = True

    # -- writes (called by tools and by finalize) ----------------------------------------------------
    async def submit(self, *, update_existing: bool | None) -> dict[str, Any]:
        missing = self.form.missing()
        if missing:
            return {"ok": False, "error": "not everything is collected yet", "still_needed": [m[1] for m in missing]}
        if not self.form.read_back_done:
            return {"ok": False, "error": "read the summary back to the caller first (call get_missing_fields), then submit after they say yes"}
        dup = self.find_duplicate()
        if dup and update_existing is None:
            return {
                "ok": False,
                "needs_decision": True,
                "error": f"this caller already has an open {self.form.destination_name()} enquiry ({_spoken_ref(dup.reference)}). "
                "Ask: should I add these details to that enquiry, or start a new one? Then call submit_lead again with update_existing true or false.",
            }
        self.scan_caller_words()
        payload = self.form.payload()
        tags = self.derived_tags()
        try:
            if dup and update_existing:
                self._patch_seq += 1
                patch = {k: v for k, v in payload.items() if k not in ("phone",)}
                patch.update({"stage": "qualified", "add_tags": tags})
                ref = await self.crm.update_enquiry(dup.id, patch, idempotency_key=self.key(f"update_enquiry:{dup.id}"))
                self.log("update_enquiry", id=dup.id, reference=ref.reference, queued=ref.queued)
                ref = EnquiryRef(dup.id, ref.reference or dup.reference, ref.stage, False, ref.queued)
            else:
                lead = {**payload, "stage": "qualified", "tags": ["voice-lead", *tags], "call_id": self.call_id, "created_by": "aanya-voice"}
                ref = await self.crm.create_enquiry(lead, idempotency_key=self.key("create_enquiry"))
                self.log("create_enquiry", id=ref.id, reference=ref.reference, created=ref.created, queued=ref.queued)
        except CRMValidationError as e:
            return self._validation_failed(e)
        except CRMUnavailable:
            self.crm_trouble = True
            return {"ok": False, "error": "the CRM is not reachable right now. Tell the caller a consultant will call them back; offer to note a callback time.", "crm_down": True}
        except CRMError as e:
            logger.error(f"submit_lead failed: {e}")
            self.crm_trouble = True
            return {"ok": False, "error": "I could not save that. Apologise briefly and offer a consultant callback."}
        self.submitted = ref
        self.linked_enquiry_id = ref.id
        if ref.queued:
            self.crm_trouble = True
            return {
                "ok": True,
                "saved": True,
                "reference_spoken": None,
                "say": "Details are saved. Our system is a little slow, so a consultant will message the reference shortly. Next, ask if there is anything else.",
            }
        return {
            "ok": True,
            "saved": True,
            "reference_spoken": _spoken_ref(ref.reference),
            "updated_existing": not ref.created if dup and update_existing else False,
            "say": f"Tell the caller their reference is {_spoken_ref(ref.reference)} and a consultant will follow up. Do not promise prices or availability.",
        }

    def _validation_failed(self, e: CRMValidationError) -> dict[str, Any]:
        for err in e.errors:
            self.form.values.pop(API_TO_FORM.get(err.field, err.field), None)
            if err.field == "phone":
                self.form.phone_confirmed = False
        self.form.read_back_done = False
        return {"ok": False, "error": e.spoken(), "reask": [x.field for x in e.errors]}

    async def callback(self, when_text: str, reason: str) -> dict[str, Any]:
        phone = self.contact_phone
        enquiry_id = self.linked_enquiry_id
        if not enquiry_id and not phone:
            return {"ok": False, "error": "ask for the best phone number first (set_lead_field phone), then schedule the callback"}
        due = p.parse_when(when_text)
        summary = f"Caller asked for a callback {('around ' + when_text) if when_text else 'soon'}. {reason}".strip()
        task = {"kind": "callback", "reason": (reason or "callback requested")[:190], "summary": summary[:1900], "add_tags": ["callback-requested"]}
        if enquiry_id and not str(enquiry_id).startswith("queued:"):
            task["enquiry_id"] = enquiry_id
        elif enquiry_id:
            task["enquiry_id"] = enquiry_id  # resolved by the outbox once the lead reaches the CRM
        else:
            task["contact_phone"] = phone
        if due:
            task["due_at"] = due.isoformat()
        try:
            ref = await self.crm.add_task(task, idempotency_key=self.key("callback"))
        except CRMValidationError as e:
            return {"ok": False, "error": e.spoken()}
        except CRMError:
            self.crm_trouble = True
            return {"ok": False, "error": "could not book the callback; apologise and say a consultant will call them soon"}
        self.callback_ref = ref.id
        self.log("callback_task", id=ref.id, due=task.get("due_at"), queued=ref.queued)
        spoken = f"{due:%A} around {_hour_spoken(due)}" if due else "soon"
        return {"ok": True, "when_spoken": spoken, "say": f"Confirm: a consultant will call {spoken}."}

    async def handoff(self, reason: str) -> dict[str, Any]:
        topic = analysis.normalise_reason(reason, self.config.escalate_on)
        self.handoff_reason = self.handoff_reason or topic
        phone = self.contact_phone
        if not self.linked_enquiry_id and not phone:
            return {"ok": True, "pending": True, "say": "Acknowledge kindly, then ask for their phone number so a consultant can call them. Do not discuss fees, refunds or policies."}
        ok = await self._write_handoff(topic, reason)
        if not ok:
            return {"ok": True, "pending": True, "say": "Acknowledge kindly and say a consultant will call them shortly. Do not discuss fees, refunds or policies."}
        return {"ok": True, "say": "Acknowledge kindly and say a consultant will call them shortly. Do not discuss fees, refunds or policies, and do not ask more sales questions."}

    async def _write_handoff(self, topic: str, reason: str) -> bool:
        if self.handoff_done:
            return True
        task: dict[str, Any] = {
            "kind": "urgent_handoff",
            "reason": topic.replace("_", " "),
            "summary": f"Voice call handed to a human. Caller said: {reason[:500]}",
            "add_tags": ["needs-human"],
        }
        if self.linked_enquiry_id:
            task["enquiry_id"] = self.linked_enquiry_id
        elif self.contact_phone:
            task["contact_phone"] = self.contact_phone
        else:
            return False
        try:
            ref = await self.crm.add_task(task, idempotency_key=self.key(f"handoff:{topic}"))
        except CRMError as e:
            logger.error(f"hand-off task failed: {e}")
            self.crm_trouble = True
            return False
        self.handoff_done = True
        self.log("urgent_handoff", id=ref.id, topic=topic, queued=ref.queued)
        return True

    # -- end of call -----------------------------------------------------------------------------------
    async def finalize(self, messages: list[Any], *, duration_s: int) -> dict[str, Any]:
        """Write everything that still has to reach the CRM. Safe to call once, at disconnect."""
        if self.finalized:
            return {}
        self.finalized = True
        await self.drain()
        self.scan_caller_words()
        transcript = analysis.build_transcript(messages)
        said = analysis.caller_text(transcript)
        caller_turns = sum(1 for t in transcript if t["speaker"] == "caller")

        # Guardrail: an escalation topic must always produce an urgent task, even if the model forgot the tool.
        topic = analysis.detect_escalation(said, self.config.escalate_on)
        if topic and not self.handoff_done:
            self.handoff_reason = self.handoff_reason or topic
        if self.handoff_reason and not self.handoff_done:
            await self._write_handoff(self.handoff_reason, said[:500])

        # Same for a requested call back: the model may answer in words instead of calling schedule_callback.
        asked = analysis.find_callback_request(transcript)
        if asked and not self.callback_ref and not self.handoff_done:
            res = await self.callback(asked, "caller asked for a call back")
            self.log("callback_safety_net", ok=res.get("ok"))

        partial = None
        if not self.submitted and self.form.has_content():
            partial = await self._save_partial()
        qualified = bool(self.submitted)
        callback_wanted = bool(self.callback_ref) or partial is not None
        if partial is not None and not self.callback_ref and not self.handoff_done:
            await self._partial_callback_task()

        outcome = analysis.decide_outcome(
            caller_turns=caller_turns,
            qualified=qualified,
            handoff=bool(self.handoff_reason),
            callback=callback_wanted,
            declined=bool(analysis.DECLINE.search(said)),
        )
        summary = self._summary(outcome, duration_s, caller_turns)
        call: dict[str, Any] = {
            "direction": "inbound",
            "handled_by": "aanya-voice",
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "duration_s": max(0, int(duration_s)),
            "status": "completed" if caller_turns else "missed",
            "outcome": outcome,
            "sentiment": analysis.decide_sentiment(outcome=outcome, handoff_reason=self.handoff_reason),
            "summary": summary,
            "transcript": transcript,
        }
        if self.linked_enquiry_id:
            call["enquiry_id"] = self.linked_enquiry_id
        if self.contact_phone:
            call["caller_phone"] = self.contact_phone
        result: dict[str, Any] = {"outcome": outcome, "summary": summary}
        try:
            ref = await self.crm.log_call(call, idempotency_key=self.key("log_call"))
            self.log("log_call", id=ref.id, outcome=outcome, queued=ref.queued)
            result["call_id"] = ref.id
            if self.linked_enquiry_id and summary:
                await self.crm.add_note(self.linked_enquiry_id, f"Voice call ({outcome}): {summary}", idempotency_key=self.key("note"))
        except CRMValidationError as e:
            logger.error(f"call log rejected: {e}")
            self.log("log_call_rejected", error=str(e))
        except CRMError as e:
            logger.error(f"call log failed: {e}")
            self.log("log_call_failed", error=str(e))
        result["transcript"] = transcript
        return result

    async def _save_partial(self) -> EnquiryRef | None:
        phone = self.contact_phone
        if not phone:
            logger.warning("partial lead not saved: no phone number for this caller")
            return None
        payload = {k: v for k, v in self.form.payload().items() if k != "phone"}
        dup = self.find_duplicate()
        try:
            if dup:
                ref = await self.crm.update_enquiry(
                    dup.id, {**payload, "add_tags": ["callback-requested", *self.derived_tags()]}, idempotency_key=self.key(f"partial_update:{dup.id}")
                )
                ref = EnquiryRef(dup.id, ref.reference or dup.reference, ref.stage, False, ref.queued)
                self.log("partial_update_enquiry", id=dup.id, queued=ref.queued)
            else:
                lead = {**payload, "phone": phone, "stage": "new", "tags": ["voice-lead", "incomplete-voice-lead", *self.derived_tags()], "call_id": self.call_id}
                ref = await self.crm.create_enquiry(lead, idempotency_key=self.key("create_enquiry"))
                self.log("partial_create_enquiry", id=ref.id, reference=ref.reference, queued=ref.queued)
        except CRMError as e:
            logger.error(f"partial lead not saved: {e}")
            self.crm_trouble = True
            return None
        self.linked_enquiry_id = ref.id
        return ref

    async def _partial_callback_task(self) -> None:
        task: dict[str, Any] = {
            "kind": "callback",
            "reason": "finish the lead details",
            "summary": "The caller dropped before all details were collected. Missing: " + ", ".join(m[1] for m in self.form.missing()),
            "add_tags": ["callback-requested"],
        }
        if self.linked_enquiry_id:
            task["enquiry_id"] = self.linked_enquiry_id
        elif self.contact_phone:
            task["contact_phone"] = self.contact_phone
        else:
            return
        try:
            ref = await self.crm.add_task(task, idempotency_key=self.key("callback"))
            self.callback_ref = ref.id
            self.log("callback_task", id=ref.id, reason="incomplete lead", queued=ref.queued)
        except CRMError as e:
            logger.error(f"partial callback task failed: {e}")

    def _summary(self, outcome: str, duration_s: int, caller_turns: int) -> str:
        v = self.form.values
        bits = []
        if v.get("destination"):
            bits.append(f"{self.form.destination_name()} {v.get('trip_type', '')} trip".replace("  ", " "))
        if v.get("adults"):
            bits.append(f"{v['adults']} adult{'s' if v['adults'] != 1 else ''}" + (f", {v['children']} child{'ren' if v['children'] != 1 else ''}" if v.get("children") else ""))
        if v.get("budget_total_inr"):
            bits.append(f"budget ₹{v['budget_total_inr']:,}")
        if v.get("departure_city"):
            bits.append(f"from {v['departure_city']}")
        facts = "; ".join(bits) if bits else "no trip details captured"
        extras = []
        if self.handoff_reason:
            extras.append(f"escalated: {self.handoff_reason.replace('_', ' ')}")
        if self.discount_asked:
            extras.append("asked about discounts")
        if self.submitted is None and self.form.has_content():
            extras.append("incomplete: needs a callback to finish")
        tail = (" (" + ", ".join(extras) + ")") if extras else ""
        mins = max(1, round(duration_s / 60)) if duration_s else 0
        return f"Aanya call, {mins} min, {caller_turns} caller turns: {facts}{tail}."[:3900]


def _spoken_ref(reference: str) -> str:
    """'WT-1058' -> 'W T ten fifty eight' so TTS reads it as a person would."""
    digits = "".join(c for c in reference if c.isdigit())
    letters = " ".join(c for c in reference if c.isalpha())
    if len(digits) == 4:
        hi, lo = int(digits[:2]), digits[2:]
        low = "hundred" if lo == "00" else (f"oh {int(lo)}" if lo[0] == "0" else _num_words(int(lo)))
        return f"{letters} {_num_words(hi)} {low}".strip()
    return f"{letters} {' '.join(digits)}".strip()


def _num_words(n: int) -> str:
    ones = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
            "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]  # fmt: skip
    tens = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    if n < 20:
        return ones[n]
    return tens[n // 10] + (f" {ones[n % 10]}" if n % 10 else "")


def _hour_spoken(d: datetime) -> str:
    h = d.hour % 12 or 12
    suffix = "a m" if d.hour < 12 else "p m"
    return f"{h}{'' if d.minute == 0 else f' {d.minute:02d}'} {suffix}"
