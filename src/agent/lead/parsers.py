"""Turn what a caller says into the values the CRM wants.

The LLM passes strings; this module is the only thing that decides what they mean. Every parser
returns a value or raises ValueError with a message that is safe to hand back to the model (and to
speak), e.g. "that phone number looks short".
"""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}  # fmt: skip
_SCALES = {"hundred": 100, "thousand": 1_000, "k": 1_000, "lakh": 100_000, "lakhs": 100_000,
           "lac": 100_000, "lacs": 100_000, "crore": 10_000_000, "crores": 10_000_000,
           "million": 1_000_000, "mil": 1_000_000}  # fmt: skip
_FILLER = {"rupees", "rupee", "rs", "inr", "about", "around", "approximately", "roughly", "nearly",
           "like", "maybe", "total", "overall", "is", "our", "my", "budget", "of", "the", "it",
           "will", "be", "so", "just", "only", "up", "to", "within", "under", "approx", "a",
           "an", "for", "all", "everything", "together", "combined", "₹"}  # fmt: skip

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"])}  # fmt: skip
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9


def today() -> date:
    return datetime.now(IST).date()


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------
def words_to_int(text: str) -> int | None:
    """'two' -> 2, 'twenty three' -> 23, '4' -> 4, 'a couple' -> 2. None if it is not a count."""
    t = text.lower().strip().replace("-", " ")
    t = re.sub(r"[.,!?]", "", t)
    if t.isdigit():
        return int(t)
    if re.fullmatch(r"(none|no|nil|nobody|nope|no one|no kids|no children|not any|zero kids|without kids)", t):
        return 0
    if t in ("a couple", "couple", "a pair", "both of us", "the two of us", "two of us"):
        return 2
    if t in ("just me", "myself", "only me", "alone", "solo", "just myself"):
        return 1
    total = 0
    seen = False
    for w in t.split():
        if w in ("and", "of", "us", "people", "persons", "adults", "adult", "kids", "children",
                 "person", "travellers", "travelers", "members", "pax", "guests", "child", "kid", "infant",
                 "infants", "babies", "baby", "toddler", "toddlers", "senior", "seniors", "old"):
            continue
        if w.isdigit():
            total += int(w)
        elif w in _UNITS:
            total += _UNITS[w]
        elif w in _TENS:
            total += _TENS[w]
        else:
            return None
        seen = True
    return total if seen else None


def parse_count(text: str, *, minimum: int = 0, maximum: int = 30, label: str = "number") -> int:
    m = re.search(r"\d+", text)
    n = int(m.group()) if m else words_to_int(text)
    if n is None:
        raise ValueError(f"I did not catch a {label}; ask again")
    if n < minimum or n > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return n


def parse_amount_inr(text: str) -> tuple[int, bool]:
    """Spoken or typed rupee amount -> (rupees, is_per_person).

    Handles: '150000', '1,50,000', '1.5 lakh', 'one and a half lakh', 'two lakh fifty thousand',
    '50k', 'eighty thousand', 'around 3 lakhs', '1 crore'.
    """
    raw = text.lower().replace("₹", " ")
    per_person = bool(re.search(r"per\s+(person|head|adult|pax)|each|a\s+head|per\s+traveller", raw))
    raw = re.sub(r"per\s+(person|head|adult|pax|traveller)|each|a\s+head", " ", raw)
    raw = raw.replace("one and a half", "1.5").replace("two and a half", "2.5")
    raw = raw.replace("three and a half", "3.5").replace("four and a half", "4.5")
    raw = raw.replace("and a half", ".5").replace("and a quarter", ".25").replace("half", "0.5")
    raw = re.sub(r"(\d),(\d)", r"\1\2", raw)  # 1,50,000 -> 150000 (repeat for chained commas)
    raw = re.sub(r"(\d),(\d)", r"\1\2", raw)
    raw = re.sub(r"(\d)(k|lakhs?|lacs?|crores?|m)\b", r"\1 \2", raw)
    tokens = re.findall(r"\d+(?:\.\d+)?|\.\d+|[a-z]+", raw.replace("-", " "))
    total = 0.0
    current = 0.0
    scaled = False
    for tok in tokens:
        if tok in _FILLER or tok == "and":
            continue
        if re.fullmatch(r"\d+(?:\.\d+)?|\.\d+", tok):
            current += float(tok)
        elif tok in _UNITS:
            current += _UNITS[tok]
        elif tok in _TENS:
            current += _TENS[tok]
        elif tok == "m":
            total += (current or 1) * 1_000_000
            current, scaled = 0.0, True
        elif tok in _SCALES:
            if tok == "hundred":
                current = (current or 1) * 100
            else:
                total += (current or 1) * _SCALES[tok]
                current, scaled = 0.0, True
        else:
            raise ValueError("I did not catch the budget; ask for it again as an amount in rupees")
    amount = int(round(total + current))
    if amount == 0:
        raise ValueError("I did not catch the budget; ask for it again as an amount in rupees")
    if not scaled and amount < 1000:
        raise ValueError(
            f"{amount} rupees is too small for a trip; confirm whether they meant thousands or lakhs"
        )
    return amount, per_person


# ---------------------------------------------------------------------------
# Phone and email
# ---------------------------------------------------------------------------
_DIGIT_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3",
                "four": "4", "for": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
                "nine": "9", "to": "2", "too": "2", "ate": "8"}  # fmt: skip


def spoken_digits(text: str) -> str:
    """'nine eight double seven' -> '98877'; digits and punctuation pass through."""
    out: list[str] = []
    mult = 1
    for tok in re.findall(r"\d+|[a-z]+|\+", text.lower()):
        if tok == "double":
            mult = 2
        elif tok == "triple":
            mult = 3
        elif tok.isdigit():
            out.append(tok * mult)
            mult = 1
        elif tok in _DIGIT_WORDS:
            out.append(_DIGIT_WORDS[tok] * mult)
            mult = 1
        elif tok == "+":
            out.append("+")
    return "".join(out)


def normalize_phone(text: str, default_country: str = "91") -> str:
    """Mirror of the CRM's normalizePhone. Raises ValueError with a speakable message."""
    raw = spoken_digits(text) if re.search(r"[a-z]", text.lower()) else text.strip()
    has_plus = raw.startswith("+")
    d = re.sub(r"\D", "", raw)
    if d.startswith("00"):
        d = d[2:]
    elif not has_plus and d.startswith("0") and len(d) == 11:
        d = d[1:]
    if len(d) == 10 and d[0] in "6789":
        d = default_country + d
    if len(d) < 10:
        raise ValueError("that phone number looks short; ask them to say all ten digits again")
    if len(d) < 11 or len(d) > 15:
        raise ValueError("that phone number does not look right; ask them to repeat it")
    return "+" + d


def parse_email(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"\s+(at the rate of|at)\s+", "@", t)
    t = re.sub(r"\s*\b(dot|period)\b\s*", ".", t)
    t = re.sub(r"\s*\b(underscore)\b\s*", "_", t)
    t = re.sub(r"\s*\b(dash|hyphen)\b\s*", "-", t)
    t = re.sub(r"\s+", "", t)
    if not re.fullmatch(r"[a-z0-9._%+\-]+@[a-z0-9\-]+(\.[a-z0-9\-]+)+", t):
        raise ValueError("that email does not look right; ask them to spell it out slowly")
    return t


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------
_DATE_RE = re.compile(
    r"(?:(?P<d1>\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s+)?(?P<m1>[a-z]{3,9})?(?:\s+(?P<y1>\d{4}))?"
    r"|(?P<m2>[a-z]{3,9})\s+(?P<d2>\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(?P<y2>\d{4}))?)",
)
_NUM_DATE_RE = re.compile(r"(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?")
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
             "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13,
             "fourteenth": 14, "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
             "eighteenth": 18, "nineteenth": 19, "twentieth": 20, "thirtieth": 30}  # fmt: skip


def _ordinals_to_digits(t: str) -> str:
    t = re.sub(r"\btwenty[\s-]+(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth)\b",
               lambda m: str(20 + _ORDINALS[m.group(1)]), t)
    t = re.sub(r"\bthirty[\s-]+first\b", "31", t)
    return re.sub(r"\b(" + "|".join(_ORDINALS) + r")\b", lambda m: str(_ORDINALS[m.group(1)]), t)


def _mk(day: int, month: int | None, year: int | None, ref: date, after: date | None) -> date:
    if month is None:
        raise ValueError("which month?")
    if year is None:
        year = ref.year
        floor = after or ref
        try:
            cand = date(year, month, day)
        except ValueError as e:
            raise ValueError("that date does not exist") from e
        if cand < floor:
            cand = date(year + 1, month, day)
        return cand
    return date(year, month, day)


def parse_date_range(text: str, ref: date | None = None) -> tuple[date, date | None]:
    """'12th to 19th December', 'December 12 to 19', '12/12 - 19/12', 'next friday' -> (start, end)."""
    ref = ref or today()
    t = _ordinals_to_digits(text.lower().replace(",", " "))
    t = re.sub(r"\b(from|starting|leaving|on|around|about|between|the)\b", " ", t)
    parts = re.split(r"\s+(?:to|till|until|through|thru|and|-|–)\s+|\s*[-–]\s*(?=[a-z\d])", t.strip(), maxsplit=1)
    if len(parts) == 1 or not re.search(r"[a-z]{3,}|\d", parts[1]):
        s = _parse_one(parts[0], ref, None)
        return s, None
    # second half may omit the month ("12th to 19th December") — borrow it from the other half
    s_try = _try_parse(parts[0], ref, None)
    e_try = _try_parse(parts[1], ref, None)
    if s_try is None and e_try is None:
        raise ValueError("I could not read those dates; ask for the start date and the end date")
    if s_try is None:
        s_try = _parse_one(parts[0] + " " + _month_word(parts[1]), ref, None)
    start = s_try
    end = e_try if e_try is not None else _parse_one(parts[1] + " " + _month_word(parts[0]), ref, start)
    if end < start:
        try:
            end = end.replace(year=end.year + 1)
        except ValueError:
            pass
    if end < start:
        raise ValueError("the end date is before the start date")
    return start, end


def _month_word(t: str) -> str:
    for w in re.findall(r"[a-z]{3,9}", t):
        if w in MONTHS:
            return w
    return ""


def _try_parse(t: str, ref: date, after: date | None) -> date | None:
    try:
        return _parse_one(t, ref, after)
    except ValueError:
        return None


def _parse_one(t: str, ref: date, after: date | None) -> date:
    t = t.strip()
    if t in ("today",):
        return ref
    if t == "tomorrow":
        return ref + timedelta(days=1)
    m = _NUM_DATE_RE.search(t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        year = (2000 + int(y) if len(y) == 2 else int(y)) if y else None
        return _mk(d, mo, year, ref, after)
    m = _DATE_RE.search(t)
    if not m:
        raise ValueError("I could not read that date")
    if m.group("d1"):
        d, mon, y = int(m.group("d1")), m.group("m1"), m.group("y1")
    else:
        d, mon, y = int(m.group("d2")), m.group("m2"), m.group("y2")
    month = MONTHS.get(mon or "")
    if month is None:
        raise ValueError("which month?")
    if not 1 <= d <= 31:
        raise ValueError("that day does not exist")
    return _mk(d, month, int(y) if y else None, ref, after)


def parse_month(text: str, ref: date | None = None) -> str:
    """'December', 'next december', 'in march 2027', '2026-12' -> 'YYYY-MM' (next occurrence)."""
    ref = ref or today()
    t = text.lower()
    m = re.search(r"(\d{4})-(\d{2})", t)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    year_m = re.search(r"\b(20\d{2})\b", t)
    for word in re.findall(r"[a-z]{3,9}", t):
        if word in MONTHS:
            mo = MONTHS[word]
            if year_m:
                y = int(year_m.group(1))
            else:
                y = ref.year if mo >= ref.month else ref.year + 1
            return f"{y}-{mo:02d}"
    raise ValueError("which month are they planning to travel in?")


# ---------------------------------------------------------------------------
# "When should we call you back?"
# ---------------------------------------------------------------------------
_WEEKDAYS = {n: i for i, n in enumerate(
    ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}  # fmt: skip
_PARTS = {"morning": (10, 0), "afternoon": (14, 0), "evening": (18, 0), "noon": (12, 0), "night": (20, 0)}


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """Best-effort callback time in IST. Returns None when nothing usable was said (the caller's own
    words still travel in the task reason)."""
    now = (now or datetime.now(IST)).astimezone(IST)
    t = text.lower()
    day: date | None = None
    if "day after tomorrow" in t:
        day = now.date() + timedelta(days=2)
    elif "tomorrow" in t:
        day = now.date() + timedelta(days=1)
    elif "today" in t or "tonight" in t or "this evening" in t or "this afternoon" in t:
        day = now.date()
    else:
        for name, idx in _WEEKDAYS.items():
            if name in t:
                delta = (idx - now.weekday()) % 7 or 7
                day = now.date() + timedelta(days=delta)
                break
    if day is None:
        try:
            day = parse_date_range(t, now.date())[0]
        except ValueError:
            day = None
    hour = minute = None
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)", t)
    if m:
        hour, minute = int(m.group(1)) % 12, int(m.group(2) or 0)
        if m.group(3).startswith("p"):
            hour += 12
    else:
        m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2))
        else:
            for part, (h, mi) in _PARTS.items():
                if part in t:
                    hour, minute = h, mi
                    break
    if day is None and hour is None:
        return None
    if day is None:
        day = now.date()
    due = datetime(day.year, day.month, day.day, hour if hour is not None else 11, minute or 0, tzinfo=IST)
    if due <= now:
        due = due + timedelta(days=1) if hour is not None and day == now.date() else now + timedelta(hours=1)
    return due


# ---------------------------------------------------------------------------
# Enumerations with spoken synonyms
# ---------------------------------------------------------------------------
_TRIP_TYPE = {
    "honeymoon": ["honeymoon", "newly married", "just married", "newlyweds"],
    "couple": ["couple", "husband", "wife", "partner", "girlfriend", "boyfriend", "anniversary",
               "romantic", "two of us", "fiance"],
    "family": ["family", "kids", "children", "child", "with my son", "with my daughter"],
    "parents": ["parents", "mom and dad", "mother and father", "mother", "father", "in-laws",
                "senior", "grandparents"],
    "friends": ["friends", "buddies", "group of", "gang", "college", "batchmates"],
    "solo": ["solo", "alone", "myself", "just me", "on my own"],
    "corporate": ["corporate", "office", "company", "team", "offsite", "colleagues", "work trip"],
}
_FOOD = {
    "non_veg": ["non veg", "non-veg", "nonveg", "non vegetarian", "non-vegetarian", "chicken",
                "meat", "fish", "seafood", "eggs"],
    "veg": ["veg", "vegetarian", "pure veg"],
    "jain": ["jain"],
    "vegan": ["vegan"],
    "halal": ["halal"],
    "no_preference": ["no preference", "anything", "no restriction", "don't mind", "dont mind",
                      "everything", "any food", "no issue", "all kinds"],
}
_FLEX = {
    "fixed": ["fixed", "exact", "cannot change", "can't change", "not flexible", "no flexibility"],
    "plus_minus_3": ["few days", "plus minus", "a couple of days", "three days", "3 days", "slightly",
                     "either way", "a day or two"],
    "flexible_month": ["flexible", "any time", "anytime", "whenever", "month", "open"],
}
_CHANNEL = {
    "whatsapp": ["whatsapp", "whats app", "watsapp"],
    "email": ["email", "mail", "e-mail"],
    "instagram": ["instagram", "insta", "dm"],
    "sms": ["sms", "text message", "text"],
    "phone": ["phone", "call", "voice"],
}
_PACE = {"slow": ["slow", "relaxed", "relax", "leisurely", "easy"],
         "packed": ["packed", "fast", "action", "see everything", "busy"],
         "balanced": ["balanced", "moderate", "mix", "medium"]}  # fmt: skip
_COMFORT = {"luxury": ["luxury", "luxurious", "five star", "5 star", "5-star"],
            "premium": ["premium", "four star", "4 star", "upscale"],
            "budget": ["budget", "cheap", "backpack", "economy", "basic"],
            "comfort": ["comfort", "standard", "mid", "three star", "3 star", "decent"]}  # fmt: skip


def _pick(table: dict[str, list[str]], text: str, label: str) -> str:
    t = text.lower().strip().replace("_", " ")
    if t.replace(" ", "_") in table:
        return t.replace(" ", "_")
    best = None
    for key, words in table.items():
        for w in words:
            if re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", t):
                if best is None or len(w) > best[1]:
                    best = (key, len(w))
    if not best:
        raise ValueError(f"I could not tell the {label} from that; ask again in simpler words")
    return best[0]


def parse_trip_type(text: str) -> str:
    return _pick(_TRIP_TYPE, text, "type of trip")


def parse_food(text: str) -> str:
    return _pick(_FOOD, text, "food preference")


def parse_flexibility(text: str) -> str:
    return _pick(_FLEX, text, "date flexibility")


def parse_channel(text: str) -> str:
    return _pick(_CHANNEL, text, "preferred channel")


def parse_pace(text: str) -> str:
    return _pick(_PACE, text, "pace")


def parse_comfort(text: str) -> str:
    return _pick(_COMFORT, text, "comfort level")


def parse_bool(text: str) -> bool:
    t = text.lower().strip()
    if re.search(r"\b(yes|yeah|yep|sure|include|please|ok|okay|correct|true)\b", t):
        return True
    if re.search(r"\b(no|nope|not|don't|dont|without|false)\b", t):
        return False
    raise ValueError("I could not tell yes or no")


def parse_name(text: str) -> str:
    t = re.sub(r"\b(my name is|this is|i am|i'm|it's|its|name is|myself|call me)\b", " ", text.lower())
    t = re.sub(r"[^a-z\s.'-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) < 2:
        raise ValueError("I need the traveller's name; ask them to say it again")
    return " ".join(w.capitalize() for w in t.split())


def parse_city(text: str) -> str:
    t = re.sub(r"\b(from|in|i am|i'm|we are|we're|flying|travelling|traveling|depart(?:ing)?|leaving|city is|staying)\b",
               " ", text.lower())
    t = re.sub(r"[^a-z\s.'-]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) < 2:
        raise ValueError("which city will they travel from? ask again")
    return " ".join(w.capitalize() for w in t.split())


def parse_interests(text: str) -> list[str]:
    parts = re.split(r",|\band\b|\bplus\b|&", text.lower())
    out = [p.strip() for p in parts if 2 <= len(p.strip()) <= 40]
    if not out:
        raise ValueError("I did not catch any interests")
    return out[:16]
