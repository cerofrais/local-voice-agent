"""Does the local LLM call the right CRM tool at the right moment, with the REAL system prompt?

    uv run python evals/tools/tool_calling.py            # 5 samples per case
    uv run python evals/tools/tool_calling.py -n 10 --model gemma4-voice

Each case is a short conversation ending on a caller turn and the set of tools that are acceptable as the
first reaction. Run it after any change to src/agent/prompts/base.py or the tool descriptions; the prompt
was chosen with this (docs/07 section 12). Needs Ollama; does not touch any CRM.
"""

import argparse
import asyncio
import json
from collections import Counter

import httpx

from agent.call.session import CallSession
from agent.config import get_settings
from agent.crm.mock import DEFAULT_DESTINATIONS, MockCRM
from agent.crm.models import AgentConfig, ContactMatch, OpenEnquiry
from agent.prompts.base import system_prompt
from agent.tools import TOOL_SCHEMAS

GREET = "Hello, I am Aanya, an automated assistant from wishtrip. How can I help you today?"
RETURNING_GREET = "Hello Priya, I am Aanya from wishtrip. Are you calling about your Kerala trip?"

CASES: list[tuple[str, str, list[tuple[str, str]], set[str]]] = [
    # (name, caller kind, conversation, acceptable first tools)
    ("place by alias", "new", [("assistant", GREET), ("user", "Hi, we are thinking of a trip to the backwaters")], {"identify_trip"}),
    ("place by name", "new", [("assistant", GREET), ("user", "I want to go to Goa")], {"identify_trip", "set_lead_field"}),
    ("confirm place", "new", [("assistant", GREET), ("user", "we want the backwaters"), ("assistant", "Do you mean Kerala?"), ("user", "Yes, Kerala")], {"set_lead_field"}),
    ("dates", "new", [("assistant", "Which dates are you planning?"), ("user", "Twelfth to the nineteenth of December")], {"set_lead_field"}),
    ("month only", "new", [("assistant", "Which dates are you planning?"), ("user", "Not fixed yet, sometime in March")], {"set_lead_field"}),
    ("headcount", "new", [("assistant", "How many adults are travelling?"), ("user", "Two adults and one child")], {"set_lead_field"}),
    ("budget", "new", [("assistant", "What is your total budget for the group?"), ("user", "Around one and a half lakh")], {"set_lead_field"}),
    ("city", "new", [("assistant", "Which city will you fly from?"), ("user", "From Pune")], {"set_lead_field"}),
    ("name", "new", [("assistant", "May I have your name?"), ("user", "Priya Sharma")], {"set_lead_field"}),
    ("several details", "new", [("assistant", GREET), ("user", "Goa for four adults in January, budget about two lakh")], {"identify_trip", "set_lead_field"}),
    ("cancel", "new", [("assistant", GREET), ("user", "I want to cancel my booking and get a refund")], {"handoff_to_human"}),
    ("complaint", "new", [("assistant", GREET), ("user", "I'm very unhappy, the hotel was terrible and I want to complain")], {"handoff_to_human"}),
    ("medical", "new", [("assistant", GREET), ("user", "My father is in hospital and we may not be able to travel")], {"handoff_to_human"}),
    ("callback", "new", [("assistant", GREET), ("user", "Can someone call me tomorrow evening instead?")], {"schedule_callback"}),
    ("submit on yes", "new", [("assistant", "So that is Kerala, 12 to 19 December, two adults and a child, 1.5 lakh from Pune, for Priya Sharma. Shall I send this to our team?"), ("user", "Yes please")], {"submit_lead"}),
    ("returning: place", "returning", [("assistant", RETURNING_GREET), ("user", "No, actually I want to plan a trip to Bali")], {"identify_trip", "set_lead_field"}),
    ("returning: cancel", "returning", [("assistant", RETURNING_GREET), ("user", "I need to cancel it")], {"handoff_to_human"}),
    ("returning: callback", "returning", [("assistant", RETURNING_GREET), ("user", "Can someone call me tomorrow evening about Bali?")], {"schedule_callback"}),
]


def make_session(kind: str) -> CallSession:
    contact = None
    if kind == "returning":
        contact = ContactMatch("trv_1", "Priya Sharma", "+919000000001", True, [OpenEnquiry("enq_1", "WT-1058", "kerala", "contacted", "Sneha", "2026-10-20T10:00:00Z")])
    return CallSession(crm=MockCRM(), config=AgentConfig(company_name="wishtrip", tone="Warm, concise, never pushy."), destinations=list(DEFAULT_DESTINATIONS), caller_phone="+919000000001", contact=contact)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5)
    ap.add_argument("--model")
    ap.add_argument("--temperature", type=float, default=0.3)
    a = ap.parse_args()
    s = get_settings()
    tools = [{"type": "function", "function": t.to_default_dict()} for t in TOOL_SCHEMAS]
    total = good = 0
    async with httpx.AsyncClient(timeout=120) as c:
        for name, kind, convo, expect in CASES:
            sess = make_session(kind)
            messages = [
                {"role": "system", "content": system_prompt(sess.config, sess)},
                *({"role": r, "content": t} for r, t in convo),
            ]
            seen: Counter = Counter()
            ok = 0
            for _ in range(a.n):
                body = {"model": a.model or s.llm_model, "messages": messages, "tools": tools, "max_tokens": 160, "temperature": a.temperature, "reasoning_effort": "none"}
                r = (await c.post(f"{s.llm_base_url}/chat/completions", json=body, headers={"Authorization": f"Bearer {s.llm_api_key}"})).json()
                m = r["choices"][0]["message"]
                names = {t["function"]["name"] for t in (m.get("tool_calls") or [])}
                seen["+".join(sorted(names)) or "(words only)"] += 1
                ok += bool(names & expect)
            total += a.n
            good += ok
            flag = "ok " if ok == a.n else ("~  " if ok else "BAD")
            print(f"{flag} {name:22s} {ok}/{a.n}   {json.dumps(dict(seen))}")
    print(f"\nright tool first: {good}/{total} ({100 * good / total:.0f}%)")


if __name__ == "__main__":
    asyncio.run(main())
