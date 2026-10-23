"""Text-only call simulator: the real prompt, tools, session and CRM, with the local LLM and no audio.

    uv run python scripts/chat_sim.py new_lead              # scripted caller against .env's CRM
    uv run python scripts/chat_sim.py --list
    uv run python scripts/chat_sim.py cancellation --crm mock
    uv run python scripts/chat_sim.py -i                    # you type the caller's lines

Checks what the audio path cannot show cheaply: does Gemma call the tools at the right moments, ask
one thing at a time, read back before submitting, and hand off the topics it must not discuss.
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime

import httpx

from agent.call.startup import start_session
from agent.config import get_settings
from agent.crm import make_crm
from agent.prompts.base import system_prompt
from agent.tools import TOOL_SCHEMAS
from agent.tools.lead_tools import LeadTools, call_tool

SCENARIOS: dict[str, list[str]] = {
    "new_lead": [
        "Hi, we are thinking of a trip to the backwaters",
        "Yes, Kerala. Sometime in the second week of December, twelfth to the nineteenth.",
        "Two adults and one child",
        "It's a family trip",
        "Around one and a half lakh for all of us",
        "We'll fly from Pune",
        "My name is Priya Sharma",
        "Yes that number is right",
        "Yes please send it",
        "No that's all, thanks",
    ],
    "cancellation": [
        "Hello, I booked a trip last month and I want to cancel it",
        "How much refund will I get?",
        "Okay please ask someone to call me",
    ],
    "dropout": [
        "Hi, I want to go to Goa",
        "For five people",
        # the caller hangs up here
    ],
    "callback": [
        "Hi, can someone from your team call me tomorrow evening about Bali?",
    ],
    "unclear_destination": [
        "I want to go somewhere with snow and mountains, not sure where",
        "Let's say Kashmir",
    ],
}


def tools_payload() -> list[dict]:
    return [{"type": "function", "function": s.to_default_dict()} for s in TOOL_SCHEMAS]


async def chat(client: httpx.AsyncClient, s, messages: list[dict]) -> dict:
    body = {
        "model": s.llm_model,
        "messages": messages,
        "tools": tools_payload(),
        "max_tokens": 160,
        "temperature": 0.3,
        "reasoning_effort": "none",
    }
    r = await client.post(f"{s.llm_base_url}/chat/completions", json=body, headers={"Authorization": f"Bearer {s.llm_api_key}"}, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]


async def say_loop(client, s, tools: LeadTools, messages: list[dict], verbose: bool) -> str:
    """Run the model until it answers in words (tool calls are executed in between)."""
    for _ in range(8):
        msg = await chat(client, s, messages)
        calls = msg.get("tool_calls") or []
        entry: dict = {"role": "assistant", "content": msg.get("content") or None}
        if calls:
            entry["tool_calls"] = calls
        messages.append(entry)
        if not calls:
            return (msg.get("content") or "").strip()
        for c in calls:
            fn = c["function"]
            args = fn.get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            result = await call_tool(tools, fn["name"], args)
            if verbose:
                print(f"    [tool] {fn['name']}({json.dumps(args, ensure_ascii=False)}) -> {json.dumps(result, ensure_ascii=False)[:300]}")
            messages.append({"role": "tool", "tool_call_id": c.get("id", ""), "content": json.dumps(result, ensure_ascii=False)})
    return "(the model kept calling tools without answering)"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", default="new_lead")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("-i", "--interactive", action="store_true")
    ap.add_argument("--crm", choices=["mock", "http"], help="override CRM_BACKEND")
    ap.add_argument("--phone", help="caller ID (default DEV_CALLER_PHONE)")
    ap.add_argument("-q", "--quiet", action="store_true", help="hide tool calls")
    a = ap.parse_args()
    if a.list:
        print("\n".join(SCENARIOS))
        return 0

    s = get_settings()
    crm = make_crm(a.crm or s.crm_backend, base_url=s.crm_base_url, api_key=s.crm_api_key, data_dir=s.data_dir)
    from agent.call.startup import caller_phone_from

    phone = caller_phone_from(type("A", (), {"body": {"caller_phone": a.phone}})() if a.phone else None, s.dev_caller_phone)
    session = await start_session(crm, phone, duplicate_window_days=s.duplicate_window_days)
    tools = LeadTools(session)
    messages: list[dict] = [{"role": "system", "content": system_prompt(session.config, session)}]
    session.messages_provider = lambda: messages

    started = datetime.now()
    async with httpx.AsyncClient() as client:
        print(f"--- {session.config.name} · caller {phone or 'unknown'} · CRM {a.crm or s.crm_backend} · call {session.call_id}\n")
        print(f"AGENT: {await say_loop(client, s, tools, messages, not a.quiet)}")
        lines = iter(SCENARIOS.get(a.scenario, [])) if not a.interactive else None
        while True:
            try:
                line = next(lines) if lines else input("CALLER: ")
            except (StopIteration, EOFError, KeyboardInterrupt):
                break
            if lines:
                print(f"CALLER: {line}")
            messages.append({"role": "user", "content": line})
            if instruction := session.guard_user_text(line):
                print("    [guard] escalation detected -> hand-off started")
                messages.append({"role": "developer", "content": instruction})
            print(f"AGENT: {await say_loop(client, s, tools, messages, not a.quiet)}")

    result = await session.finalize(messages, duration_s=int((datetime.now() - started).total_seconds()))
    print("\n--- call over")
    print(f"outcome : {result.get('outcome')}")
    print(f"summary : {result.get('summary')}")
    print(f"lead    : {json.dumps(session.form.values, ensure_ascii=False)}")
    print("CRM writes:")
    for e in session.events:
        print(f"  {json.dumps(e, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
