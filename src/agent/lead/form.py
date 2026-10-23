"""The lead sheet being filled during one call.

`set(name, value)` is the only way a field gets in: it parses what the caller said, checks it, and
stores the CRM-ready value. The LLM never builds a payload; `payload()` does.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from agent.crm.models import Destination
from agent.lead import parsers as p
from agent.lead.destinations import DestinationResolver

# Order to ask in; each item is (field the model may set, label spoken to the caller).
REQUIRED_ORDER = [
    ("destination", "where they want to go"),
    ("travel_dates", "when they want to travel (dates, or just a month)"),
    ("adults", "how many adults are travelling"),
    ("children", "whether any children are travelling"),
    ("trip_type", "what kind of trip it is (couple, family, friends, honeymoon...)"),
    ("budget_total_inr", "the total budget for the whole group"),
    ("departure_city", "which city they will travel from"),
    ("full_name", "their name"),
    ("phone", "the best phone number"),
]
OPTIONAL = ["email", "nights", "food", "preferred_channel", "notes", "flexibility", "rooms", "infants", "interests"]

# Names the model may use -> canonical names the form stores.
ALIASES = {
    "name": "full_name", "traveller_name": "full_name", "customer_name": "full_name",
    "budget": "budget_total_inr", "budget_inr": "budget_total_inr", "total_budget": "budget_total_inr",
    "city": "departure_city", "from_city": "departure_city", "origin": "departure_city",
    "departure": "departure_city", "travelling_from": "departure_city",
    "dates": "travel_dates", "travel_date": "travel_dates", "when": "travel_dates",
    "month": "travel_month", "travel_month": "travel_month",
    "people": "adults", "number_of_adults": "adults", "adult": "adults", "pax": "adults",
    "kids": "children", "child": "children", "number_of_children": "children",
    "type": "trip_type", "trip": "trip_type", "occasion": "trip_type",
    "food_preference": "food", "diet": "food", "food_pref": "food",
    "mobile": "phone", "phone_number": "phone", "contact_number": "phone",
    "mail": "email", "email_address": "email",
    "channel": "preferred_channel", "contact_preference": "preferred_channel",
    "note": "notes", "special_requests": "notes", "requests": "notes",
    "place": "destination", "where": "destination", "trip_destination": "destination",
    "length": "nights", "duration": "nights", "number_of_nights": "nights",
}  # fmt: skip

SETTABLE = sorted({k for k, _ in REQUIRED_ORDER} | set(OPTIONAL) | {"travel_month", "pace", "comfort", "include_flights"})


@dataclass
class SetResult:
    ok: bool
    message: str
    field: str = ""


@dataclass
class LeadForm:
    resolver: DestinationResolver
    today: Callable[[], date] = p.today
    values: dict[str, Any] = field(default_factory=dict)
    phone_confirmed: bool = False
    read_back_done: bool = False
    touched: set[str] = field(default_factory=set)  # fields the caller actually told us (not caller ID)

    # -- writing ---------------------------------------------------------------------------
    def prefill(self, **known: Any) -> None:
        """Values known before the call (caller ID, CRM contact). They do not count as 'touched'."""
        for k, v in known.items():
            if v:
                self.values[k] = v

    def set(self, name: str, value: str) -> SetResult:
        key = ALIASES.get(name.strip().lower().replace(" ", "_"), name.strip().lower().replace(" ", "_"))
        if key not in SETTABLE:
            return SetResult(False, f"unknown field '{name}'. Use one of: {', '.join(SETTABLE)}", key)
        text = str(value).strip()
        if not text:
            return SetResult(False, f"no value given for {key}", key)
        try:
            msg = self._apply(key, text)
        except ValueError as e:
            return SetResult(False, str(e), key)
        self.touched.add(key)
        self.read_back_done = False  # any change invalidates a previous read-back
        return SetResult(True, msg, key)

    def _apply(self, key: str, text: str) -> str:
        v = self.values
        if key == "destination":
            matches = self.resolver.resolve(text)
            if not matches:
                raise ValueError(
                    "that is not a destination we sell. Offer: " + ", ".join(self.resolver.names())
                )
            top = matches[0]
            if len(matches) > 1 and matches[1].score >= top.score:
                raise ValueError("it could be " + " or ".join(m.destination.name for m in matches[:3]) + "; ask which one")
            v["destination"] = top.destination.code
            return f"destination set to {top.destination.name}"
        if key == "travel_dates":
            start, end = p.parse_date_range(text, self.today())
            if start < self.today():
                raise ValueError("that date is in the past; ask for a future date")
            v["travel_start"] = start.isoformat()
            v.pop("month_if_flexible", None)
            if end:
                v["travel_end"] = end.isoformat()
                v["nights"] = max(1, (end - start).days)
            else:
                v.pop("travel_end", None)
            span = f" to {end:%d %B}" if end else ""
            return f"travel dates set to {start:%d %B %Y}{span}" + ("" if end else "; ask for the return date or number of nights")
        if key == "travel_month":
            month = p.parse_month(text, self.today())
            v["month_if_flexible"] = month
            for k in ("travel_start", "travel_end"):
                v.pop(k, None)
            v.setdefault("flexibility", "flexible_month")
            return f"travel month set to {month}"
        if key == "nights":
            v["nights"] = p.parse_count(text, minimum=1, maximum=60, label="nights")
            if v.get("travel_start") and not v.get("travel_end"):
                start = date.fromisoformat(v["travel_start"])
                v["travel_end"] = date.fromordinal(start.toordinal() + v["nights"]).isoformat()
            return f"{v['nights']} nights"
        if key in ("adults", "children", "infants", "rooms"):
            lo = 1 if key in ("adults", "rooms") else 0
            v[key] = p.parse_count(text, minimum=lo, maximum=30 if key != "infants" else 10, label=key)
            return f"{key} set to {v[key]}"
        if key == "trip_type":
            v["trip_type"] = p.parse_trip_type(text)
            return f"trip type set to {v['trip_type']}"
        if key == "budget_total_inr":
            amount, per_person = p.parse_amount_inr(text)
            if per_person:
                n = v.get("adults")
                if not n:
                    raise ValueError("that was per person; ask how many people are travelling first, then set the total budget")
                amount *= n + v.get("children", 0)
            if amount > 100_000_000:
                raise ValueError("that budget is too large; confirm the amount")
            v["budget_total_inr"] = amount
            return f"budget set to {amount} rupees for the whole group"
        if key == "departure_city":
            v["departure_city"] = p.parse_city(text)
            return f"departing from {v['departure_city']}"
        if key == "full_name":
            v["full_name"] = p.parse_name(text)
            return f"name set to {v['full_name']}"
        if key == "phone":
            v["phone"] = p.normalize_phone(text)
            self.phone_confirmed = True
            return f"phone set to {v['phone']}"
        if key == "email":
            v["email"] = p.parse_email(text)
            return f"email set to {v['email']}"
        if key == "food":
            v["food"] = p.parse_food(text)
            return f"food preference set to {v['food']}"
        if key == "flexibility":
            v["flexibility"] = p.parse_flexibility(text)
            return f"date flexibility set to {v['flexibility']}"
        if key == "preferred_channel":
            v["preferred_channel"] = p.parse_channel(text)
            return f"preferred channel set to {v['preferred_channel']}"
        if key == "pace":
            v["pace"] = p.parse_pace(text)
            return f"pace set to {v['pace']}"
        if key == "comfort":
            v["comfort"] = p.parse_comfort(text)
            return f"comfort set to {v['comfort']}"
        if key == "include_flights":
            v["include_flights"] = p.parse_bool(text)
            return f"include flights: {v['include_flights']}"
        if key == "interests":
            v["interests"] = p.parse_interests(text)
            return "interests noted"
        if key == "notes":
            old = v.get("notes")
            v["notes"] = (old + ". " if old else "") + text[:500]
            return "note added"
        raise ValueError("cannot set that field")

    def confirm_phone(self) -> None:
        """The caller said the number we already have (caller ID) is right."""
        if self.values.get("phone"):
            self.phone_confirmed = True

    # -- reading ---------------------------------------------------------------------------
    def missing(self) -> list[tuple[str, str]]:
        v = self.values
        out: list[tuple[str, str]] = []
        for key, label in REQUIRED_ORDER:
            if key == "travel_dates":
                if not (v.get("travel_start") or v.get("month_if_flexible")):
                    out.append((key, label))
            elif key == "phone":
                # A caller-ID number counts: it is read back inside the final summary, where a "no" corrects it.
                if not v.get("phone"):
                    out.append((key, label))
            elif key not in v:
                out.append((key, label))
        return out

    def has_content(self) -> bool:
        """Did the caller tell us anything worth saving if the call drops?"""
        return bool(self.touched - {"phone"})

    def destination_name(self) -> str:
        d = self.resolver.by_code(self.values.get("destination", ""))
        return d.name if d else ""

    def summary(self) -> str:
        """One short sentence-run the model can read aloud nearly verbatim (long read-backs sound endless on a call)."""
        v = self.values
        when = ""
        if v.get("travel_start"):
            s = date.fromisoformat(v["travel_start"])
            e = date.fromisoformat(v["travel_end"]) if v.get("travel_end") else None
            when = f"{s:%d %B}" + (f" to {e:%d %B}" if e else "")
        elif v.get("month_if_flexible"):
            y, m = v["month_if_flexible"].split("-")
            when = f"{date(int(y), int(m), 1):%B %Y}, dates flexible"
        pax = f"{v.get('adults', '?')} adult{'s' if v.get('adults') != 1 else ''}"
        if v.get("children"):
            pax += f" and {v['children']} child{'ren' if v['children'] != 1 else ''}"
        parts = [
            f"{v.get('full_name', '?')}, number {p_spoken(v.get('phone', ''))}.",
            f"{self.destination_name() or '?'}, {when or '?'}.",
            f"{pax}, {v.get('trip_type', '?')} trip.",
            f"Budget {inr_spoken(v['budget_total_inr']) if 'budget_total_inr' in v else '?'} for everyone, from {v.get('departure_city', '?')}.",
        ]
        if v.get("food"):
            parts.append(f"Food: {v['food'].replace('_', ' ')}.")
        if v.get("email"):
            parts.append(f"Email {v['email']}.")
        return " ".join(parts)

    # -- building the CRM payload ---------------------------------------------------------------
    def payload(self) -> dict[str, Any]:
        keep = {
            "full_name", "phone", "email", "destination", "travel_start", "travel_end",
            "month_if_flexible", "nights", "flexibility", "adults", "children", "infants", "rooms",
            "trip_type", "pace", "comfort", "food", "budget_total_inr", "departure_city",
            "include_flights", "interests", "notes", "preferred_channel", "city",
        }  # fmt: skip
        return {k: v for k, v in self.values.items() if k in keep}


_DIGIT_WORD = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]


def p_spoken(phone: str) -> str:
    """'+919876543210' -> 'nine eight seven six five four three two one zero'.

    Written out as words so the model copies them instead of mis-reading digits. Indian numbers drop
    the +91; others keep their country code after 'plus'.
    """
    if not phone:
        return "?"
    digits = phone.removeprefix("+")
    prefix = ""
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    else:
        prefix = "plus "
    return prefix + " ".join(_DIGIT_WORD[int(d)] for d in digits if d.isdigit())


def inr_spoken(amount: int) -> str:
    if amount >= 10_000_000:
        c = amount / 10_000_000
        return f"{c:g} crore rupees"
    if amount >= 100_000:
        lk = amount / 100_000
        return f"{lk:g} lakh rupees"
    if amount >= 1000:
        return f"{amount / 1000:g} thousand rupees"
    return f"{amount} rupees"


def destinations_text(dests: list[Destination]) -> str:
    return ", ".join(d.name for d in dests)
