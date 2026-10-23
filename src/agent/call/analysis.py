"""Pure functions that turn a finished conversation into CRM facts. No I/O, easy to unit-test."""

import re
from typing import Any

# CRM topic (Settings -> Agents -> Voice -> escalate on) -> what a caller says about it.
TOPIC_PATTERNS: dict[str, re.Pattern[str]] = {
    "cancellation": re.compile(r"\bcancel(?:l?ation|l?ed|l?ing|s)?\b", re.IGNORECASE),
    "refund": re.compile(r"\brefund(?:s|ed|ing)?\b|\bmoney back\b", re.IGNORECASE),
    "complaint": re.compile(
        r"\b(?:complain(?:t|ts|ing)?|unacceptable|terrible service|worst|fraud|cheated|scam|"
        r"legal action|consumer court|escalate)\b",
        re.IGNORECASE,
    ),
    "medical": re.compile(
        r"\b(?:medical|hospital|doctor|surgery|pregnan\w*|illness|injur\w*|emergency|ambulance|"
        r"heart (?:attack|problem)|chemotherapy|oxygen)\b",
        re.IGNORECASE,
    ),
    "payment_dispute": re.compile(
        r"(?:charged twice|double[- ]charged|money (?:was )?deducted|payment (?:failed|dispute|"
        r"not (?:received|showing))|chargeback|wrong amount (?:was )?charged)",
        re.IGNORECASE,
    ),
    "visa_rejection": re.compile(
        r"visa.{0,30}(?:reject\w*|den(?:y|ied)|refus\w*)|(?:reject\w*|den(?:y|ied)|refus\w*).{0,30}visa",
        re.IGNORECASE,
    ),
}
DISCOUNT = re.compile(r"\b(?:discount|cheaper|reduce the price|best price|negotiat\w*|offer)\b", re.IGNORECASE)
PRICE_ASK = re.compile(r"\b(?:how much|price|cost|rates?|quote)\b", re.IGNORECASE)
CALLBACK_ASK = re.compile(
    r"\b(?:(?:call|ring|phone)\s+(?:me|us)(?:\s+back)?|call\s*back|callback|give\s+(?:me|us)\s+a\s+call|"
    r"(?:talk|speak)\s+to\s+(?:a|an|some)\s*(?:one|person|human|consultant|agent|executive)|"
    r"have\s+(?:someone|somebody|a consultant|your team)\s+(?:call|phone|ring))",
    re.I,
)
DECLINE = re.compile(r"\b(?:not interested|no thanks|don'?t call|do not call|stop calling|wrong number)\b", re.IGNORECASE)


def topic_key(topic: str) -> str:
    return topic.strip().lower().replace(" ", "_").replace("-", "_")


def detect_escalation(text: str, topics: list[str]) -> str | None:
    """First CRM-enabled escalation topic the caller's words touch, or None."""
    wanted = {topic_key(t) for t in topics}
    for key, pat in TOPIC_PATTERNS.items():
        if key in wanted and pat.search(text):
            return key
    return None


def normalise_reason(reason: str, topics: list[str]) -> str:
    """The model's free-text reason -> a topic key when it names one, else 'other'."""
    hit = detect_escalation(reason, topics) or detect_escalation(reason, list(TOPIC_PATTERNS))
    if hit:
        return hit
    if DISCOUNT.search(reason):
        return "discount"
    return "other"


def mask_pii(text: str) -> str:
    """Same rules as the CRM's maskPII so transcripts are clean before they leave this machine."""
    text = re.sub(r"\b[A-Z]\d{7}\b", lambda m: "••••" + m.group()[-4:], text)
    return re.sub(r"\b\d(?:[ -]?\d){11,18}\b", lambda m: "••••" + re.sub(r"\D", "", m.group())[-4:], text)


def build_transcript(messages: list[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else None
        content = m.get("content") if isinstance(m, dict) else None
        if role not in ("user", "assistant") or not isinstance(content, str) or not content.strip():
            continue
        out.append({"speaker": "caller" if role == "user" else "agent", "text": mask_pii(content.strip())[:3900]})
    return out[:600]


def find_callback_request(transcript: list[dict[str, str]]) -> str | None:
    """The caller utterance in which they asked for a call back, if any."""
    for t in transcript:
        if t["speaker"] == "caller" and CALLBACK_ASK.search(t["text"]):
            return t["text"]
    return None


def caller_text(transcript: list[dict[str, str]]) -> str:
    return " ".join(t["text"] for t in transcript if t["speaker"] == "caller")


def decide_outcome(*, caller_turns: int, qualified: bool, handoff: bool, callback: bool, declined: bool) -> str:
    if handoff:
        return "handoff"
    if qualified:
        return "qualified"
    if callback:
        return "callback"
    if declined:
        return "not_interested"
    if caller_turns == 0:
        return "no_answer"
    return "info"


def decide_sentiment(*, outcome: str, handoff_reason: str | None) -> str:
    if handoff_reason in ("complaint", "refund", "cancellation", "payment_dispute", "visa_rejection"):
        return "negative"
    if outcome in ("qualified", "booked"):
        return "positive"
    return "neutral"
