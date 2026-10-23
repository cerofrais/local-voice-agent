"""The tools the LLM can call. Few, flat and narrow (docs/07 section 5).

Every method takes strings the model heard and returns a small dict. Parsing, validation and CRM
payloads are code (`LeadForm`, `CallSession`); the model never sees an API shape. Returned dicts carry
a `next` / `say` hint so the model asks exactly one thing next.
"""

from typing import Any

from loguru import logger

from agent.call.session import CallSession, _spoken_ref
from agent.crm.models import CRMError

SET_FIELDS = (
    "destination, travel_dates, travel_month, nights, adults, children, trip_type, budget, "
    "departure_city, full_name, phone, email, food, notes"
)


class LeadTools:
    def __init__(self, session: CallSession):
        self.s = session

    # -- read -----------------------------------------------------------------------------------------
    async def identify_trip(self, description: str) -> dict[str, Any]:
        """Which destination is the caller talking about?"""
        cands = self.s.resolver.resolve(description)[:3]
        if not cands:
            return {
                "matched": False,
                "say": "No match. Ask which place they have in mind; we offer: " + ", ".join(self.s.resolver.names()),
            }
        top = cands[0]
        exact = top.matched in (top.destination.code, top.destination.name.lower())
        clear = len(cands) == 1 or top.score - cands[1].score >= 0.15
        if exact and clear:
            # The caller said the destination's own name: no need to make them confirm it.
            res = self.s.form.set("destination", top.destination.name)
            if res.ok:
                out_exact: dict[str, Any] = {"matched": True, "destination_set": top.destination.name, **self._next()}
                if top.destination.from_price_inr:
                    out_exact["price_rule"] = (
                        f"Only if asked: 'from rupees {top.destination.from_price_inr} per person, subject to availability'. Never promise a final price."
                    )
                existing = [e for e in self.s.open_enquiries() if e.destination == top.destination.code]
                if existing:
                    out_exact["caller_has_open_enquiry"] = f"{_spoken_ref(existing[0].reference)}, stage {existing[0].stage}"
                return out_exact
        out = []
        for c in cands:
            d = c.destination
            row: dict[str, Any] = {"destination": d.name, "matched_on": c.matched}
            if d.from_price_inr:
                row["from_price_per_person_inr"] = d.from_price_inr
            out.append(row)
        existing = [e for e in self.s.open_enquiries() if e.destination in {c.destination.code for c in cands}]
        res: dict[str, Any] = {
            "matched": True,
            "candidates": out,
            "say": f"Confirm with the caller: {cands[0].destination.name}"
            + (f" or {cands[1].destination.name}" if len(cands) > 1 else "")
            + "? Then call set_lead_field destination.",
        }
        if existing:
            res["caller_has_open_enquiry"] = f"{_spoken_ref(existing[0].reference)} for {existing[0].destination}, stage {existing[0].stage}"
        if any("from_price_per_person_inr" in r for r in out):
            res["price_rule"] = "Only if asked: say 'from rupees X per person, subject to availability'. Never promise a final price."
        return res

    async def get_missing_fields(self) -> dict[str, Any]:
        """What is still needed, in the order to ask. When nothing is missing, returns the summary to read back."""
        missing = self.s.form.missing()
        if missing:
            return {
                "all_collected": False,
                "next_to_ask": missing[0][1],
                "still_needed": [m[1] for m in missing],
                "tip": "Ask for one thing only.",
            }
        self.s.form.read_back_done = True
        return {
            "all_collected": True,
            "read_back": self.s.form.summary(),
            "say": "Read the read_back text aloud almost word for word (it is already short), then ask 'shall I send this to our team?'. Do not add anything else. If they correct anything, call set_lead_field with the fix. After a clear yes call submit_lead with caller_confirmed true.",
        }

    # -- local writes ---------------------------------------------------------------------------------
    async def set_lead_field(self, name: str, value: str) -> dict[str, Any]:
        """Store one detail the caller gave. Pass their words as the value; code parses them."""
        key = name.strip().lower().replace(" ", "_")
        if key in ("phone", "phone_number", "mobile") and value.strip().lower() in ("yes", "correct", "right", "same", "this one", "that one"):
            self.s.form.confirm_phone()
            return {"ok": True, "message": "number confirmed", **self._next()}
        res = self.s.form.set(name, value)
        out: dict[str, Any] = {"ok": res.ok, "message": res.message}
        if res.ok:
            out.update(self._next())
        return out

    def _next(self) -> dict[str, Any]:
        missing = self.s.form.missing()
        if not missing:
            return {"all_collected": True, "next": "Call get_missing_fields to get the summary to read back."}
        return {"next_to_ask": missing[0][1]}

    # -- CRM writes ---------------------------------------------------------------------------------
    async def submit_lead(self, caller_confirmed: bool = False, update_existing: bool | None = None) -> dict[str, Any]:
        """Send the confirmed details to the CRM team. Only after the caller said yes to the read-back."""
        if not _truthy(caller_confirmed):
            return {"ok": False, "error": "do not submit until the caller has said yes to the read-back"}
        return await self.s.submit(update_existing=None if update_existing is None else _truthy(update_existing))

    async def schedule_callback(self, when: str = "", reason: str = "") -> dict[str, Any]:
        """Book a call from a human consultant at the time the caller asked for."""
        return await self.s.callback(when, reason)

    async def handoff_to_human(self, reason: str) -> dict[str, Any]:
        """Escalate (cancellation, refund, complaint, medical, payment problem, visa rejection...) to a consultant."""
        return await self.s.handoff(reason)


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "y", "1")
    return bool(v)


async def call_tool(tools: LeadTools, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one tool and never let an exception reach the pipeline."""
    fn = getattr(tools, name, None)
    if fn is None or name.startswith("_"):
        return {"ok": False, "error": f"unknown tool {name}"}
    try:
        return await fn(**{k: v for k, v in (arguments or {}).items() if k in fn.__code__.co_varnames[1 : fn.__code__.co_argcount]})
    except TypeError as e:
        logger.warning(f"bad arguments for {name}: {e}")
        return {"ok": False, "error": "wrong arguments for this tool; try again with the documented ones"}
    except CRMError as e:
        logger.error(f"{name}: CRM error {e}")
        return {"ok": False, "error": "the system had a problem; apologise briefly and offer a consultant callback"}
    except Exception as e:  # a tool bug must not drop the call
        logger.exception(f"{name} failed: {e}")
        return {"ok": False, "error": "something went wrong; apologise briefly and continue"}
