"""In-memory CRM used by tests and first demos. It enforces the same rules as the real API so the
agent cannot pass against the mock and fail against the CRM: required fields, enums, phone
normalisation, idempotent writes, and the same error shape."""

import json
from copy import deepcopy
from itertools import count
from pathlib import Path

from agent.crm.models import (
    AgentConfig,
    CallRef,
    ContactMatch,
    CRMNotFound,
    CRMValidationError,
    Destination,
    EnquiryRef,
    FieldError,
    OpenEnquiry,
    Payload,
    TaskRef,
)
from agent.lead import parsers as p

REQUIRED = ["full_name", "phone", "destination", "adults", "trip_type", "budget_total_inr", "departure_city"]
ENUMS = {
    "trip_type": {"couple", "honeymoon", "family", "friends", "solo", "parents", "corporate"},
    "food": {"veg", "non_veg", "jain", "vegan", "halal", "no_preference"},
    "flexibility": {"fixed", "plus_minus_3", "flexible_month"},
    "preferred_channel": {"whatsapp", "instagram", "email", "sms", "phone"},
}
AGENT_STAGES = {"new", "contacted", "qualified"}

DEFAULT_DESTINATIONS = [
    Destination("goa", "Goa", ["goa", "north goa", "south goa", "baga", "calangute", "panjim"], 28000),
    Destination("kerala", "Kerala", ["kerala", "backwaters", "god's own country", "munnar", "alleppey", "kochi", "houseboat"], 44500),
    Destination("dharamshala", "Dharamshala", ["dharamshala", "mcleodganj", "dalhousie", "himachal"], 22000),
    Destination("thailand", "Thailand", ["thailand", "phuket", "bangkok", "pattaya", "krabi"], 52000),
    Destination("japan", "Japan", ["japan", "tokyo", "kyoto", "osaka", "mount fuji"], 145000),
    Destination("vietnam", "Vietnam", ["vietnam", "hanoi", "ho chi minh", "da nang", "hoi an", "ha long bay"], 62000),
    Destination("bali", "Bali", ["bali", "ubud", "seminyak", "nusa penida", "indonesia"], 58000),
    Destination("malaysia", "Malaysia", ["malaysia", "kuala lumpur", "langkawi", "penang"], 54000),
    Destination("dubai", "Dubai", ["dubai", "abu dhabi", "uae", "desert safari"], 68000),
    Destination("kashmir", "Kashmir", ["kashmir", "srinagar", "gulmarg", "pahalgam", "dal lake"], 36000),
    Destination("ladakh", "Ladakh", ["ladakh", "leh", "pangong", "nubra", "khardung la"], 41000),
    Destination("andaman", "Andaman", ["andaman", "port blair", "havelock", "neil island"], 49000),
]


class MockCRM:
    def __init__(self, store: Path | None = None, config: AgentConfig | None = None):
        self.config = config or AgentConfig()
        self.contacts: dict[str, dict] = {}  # phone key -> {id, name, phone}
        self.enquiries: dict[str, Payload] = {}
        self.tasks: list[Payload] = []
        self.calls: list[Payload] = []
        self.notes: list[tuple[str, str]] = []
        self.idem: dict[str, object] = {}
        self.write_count = 0
        self.store = store
        self._ids = count(1)
        self._refs = count(1101)
        self.down = False  # tests flip this to simulate an outage

    # -- helpers ----------------------------------------------------------------------------
    def _guard(self) -> None:
        if self.down:
            from agent.crm.models import CRMUnavailable

            raise CRMUnavailable("mock CRM is down")

    def _idem(self, key: str):
        return self.idem.get(key)

    def _save(self) -> None:
        if self.store:
            self.store.parent.mkdir(parents=True, exist_ok=True)
            self.store.write_text(
                json.dumps(
                    {"enquiries": self.enquiries, "tasks": self.tasks, "calls": self.calls, "notes": self.notes},
                    indent=2,
                    default=str,
                )
            )

    def _validate(self, data: Payload, *, require: bool) -> Payload:
        errors: list[FieldError] = []
        out = deepcopy(data)
        if "phone" in out:
            try:
                out["phone"] = p.normalize_phone(out["phone"])
            except ValueError:
                errors.append(FieldError("phone", "that phone number looks wrong — it needs at least 10 digits"))
        if require:
            for f in REQUIRED:
                if out.get(f) in (None, ""):
                    errors.append(FieldError(f, f"{f.replace('_', ' ')} is required"))
        if "destination" in out and out["destination"] not in {d.code for d in DEFAULT_DESTINATIONS}:
            errors.append(FieldError("destination", "not a destination we sell"))
        for f, allowed in ENUMS.items():
            if f in out and out[f] not in allowed:
                errors.append(FieldError(f, f"must be one of {sorted(allowed)}"))
        if "adults" in out and not (isinstance(out["adults"], int) and out["adults"] >= 1):
            errors.append(FieldError("adults", "at least one adult is needed"))
        if "budget_total_inr" in out and not (isinstance(out["budget_total_inr"], int) and out["budget_total_inr"] >= 0):
            errors.append(FieldError("budget_total_inr", "budget can't be negative"))
        if "stage" in out and out["stage"] not in AGENT_STAGES:
            errors.append(FieldError("stage", "agents may only set new, contacted or qualified"))
        if errors:
            raise CRMValidationError(errors)
        return out

    # -- reads ----------------------------------------------------------------------------------
    async def get_config(self) -> AgentConfig:
        self._guard()
        return self.config

    async def lookup_contact(self, phone: str) -> ContactMatch | None:
        self._guard()
        key = p.normalize_phone(phone)[-10:]
        c = self.contacts.get(key)
        if not c:
            return None
        return ContactMatch(c["id"], c["name"], c["phone"], c.get("returning", False), await self.list_open_enquiries(c["id"]))

    async def list_open_enquiries(self, contact_id: str) -> list[OpenEnquiry]:
        self._guard()
        return [
            OpenEnquiry(e["id"], e["reference"], e.get("destination", "tbd"), e["stage"], "Sneha", e.get("updated_at"), e.get("month_if_flexible"))
            for e in self.enquiries.values()
            if e.get("contact_id") == contact_id
        ]

    async def list_destinations(self) -> list[Destination]:
        self._guard()
        return list(DEFAULT_DESTINATIONS)

    async def get_enquiry(self, enquiry_id: str) -> Payload:
        self._guard()
        if enquiry_id not in self.enquiries:
            raise CRMNotFound(enquiry_id)
        return deepcopy(self.enquiries[enquiry_id])

    # -- writes ---------------------------------------------------------------------------------
    async def create_enquiry(self, lead: Payload, *, idempotency_key: str) -> EnquiryRef:
        self._guard()
        if (hit := self._idem(idempotency_key)) is not None:
            ref = deepcopy(hit)
            ref.created = False  # type: ignore[attr-defined]
            return ref  # type: ignore[return-value]
        incomplete = "incomplete-voice-lead" in lead.get("tags", [])
        data = self._validate(lead, require=not incomplete)
        key = data["phone"][-10:]
        contact = self.contacts.get(key)
        if not contact:
            contact = {"id": f"trv_{next(self._ids)}", "name": data.get("full_name") or data["phone"], "phone": data["phone"]}
            self.contacts[key] = contact
        eid = f"enq_{next(self._ids)}"
        rec = {**data, "id": eid, "reference": f"WT-{next(self._refs)}", "stage": data.get("stage", "new"),
               "contact_id": contact["id"], "source": "phone_call", "created_by": "aanya-voice"}
        self.enquiries[eid] = rec
        self.write_count += 1
        ref = EnquiryRef(eid, rec["reference"], rec["stage"], True)
        self.idem[idempotency_key] = ref
        self._save()
        return ref

    async def update_enquiry(self, enquiry_id: str, patch: Payload, *, idempotency_key: str) -> EnquiryRef:
        self._guard()
        if (hit := self._idem(idempotency_key)) is not None:
            ref = deepcopy(hit)
            ref.created = False  # type: ignore[attr-defined]
            return ref  # type: ignore[return-value]
        if enquiry_id not in self.enquiries:
            raise CRMNotFound(enquiry_id)
        data = self._validate(patch, require=False)
        e = self.enquiries[enquiry_id]
        add_tags = data.pop("add_tags", [])
        e.update(data)
        e["tags"] = sorted({*e.get("tags", []), *add_tags})
        self.write_count += 1
        ref = EnquiryRef(enquiry_id, e["reference"], e["stage"], False)
        self.idem[idempotency_key] = ref
        self._save()
        return ref

    async def add_note(self, enquiry_id: str, text: str, *, idempotency_key: str) -> None:
        self._guard()
        if idempotency_key in self.idem:
            return
        if enquiry_id not in self.enquiries:
            raise CRMNotFound(enquiry_id)
        self.notes.append((enquiry_id, text))
        self.idem[idempotency_key] = True
        self._save()

    async def add_task(self, task: Payload, *, idempotency_key: str) -> TaskRef:
        self._guard()
        if (hit := self._idem(idempotency_key)) is not None:
            return hit  # type: ignore[return-value]
        errors = []
        if task.get("kind") not in ("callback", "urgent_handoff"):
            errors.append(FieldError("kind", "must be callback or urgent_handoff"))
        if not task.get("enquiry_id") and not task.get("contact_phone"):
            errors.append(FieldError("enquiry_id", "give an enquiry or a contact phone"))
        if errors:
            raise CRMValidationError(errors)
        ref = TaskRef(f"task_{next(self._ids)}", "Sneha Iyer")
        self.tasks.append({**task, "id": ref.id})
        if task.get("enquiry_id") in self.enquiries and "needs-human" in task.get("add_tags", []):
            e = self.enquiries[task["enquiry_id"]]
            e["tags"] = sorted({*e.get("tags", []), "needs-human"})
        self.idem[idempotency_key] = ref
        self._save()
        return ref

    async def log_call(self, call: Payload, *, idempotency_key: str) -> CallRef:
        self._guard()
        if (hit := self._idem(idempotency_key)) is not None:
            return hit  # type: ignore[return-value]
        missing = [f for f in ("direction", "started_at", "duration_s", "outcome") if f not in call]
        if missing:
            raise CRMValidationError([FieldError(f, f"{f} is required") for f in missing])
        ref = CallRef(f"call_{next(self._ids)}")
        self.calls.append({**call, "id": ref.id})
        self.idem[idempotency_key] = ref
        self._save()
        return ref

    # -- test seeding ----------------------------------------------------------------------------
    def seed_contact(self, name: str, phone: str, *, returning: bool = False, enquiry: Payload | None = None) -> str:
        phone = p.normalize_phone(phone)
        cid = f"trv_{next(self._ids)}"
        self.contacts[phone[-10:]] = {"id": cid, "name": name, "phone": phone, "returning": returning}
        if enquiry:
            eid = f"enq_{next(self._ids)}"
            self.enquiries[eid] = {"id": eid, "reference": f"WT-{next(self._refs)}", "stage": "contacted",
                                   "contact_id": cid, "updated_at": "2026-10-20T10:00:00Z", **enquiry}
        return cid
