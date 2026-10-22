"""LLM bake-off through Ollama: tool-call accuracy, grounding, brevity, and latency.

Run: uv run python evals/bench/bench_llm.py gemma4:latest qwen3.5:9b [--repeats 3]
Thinking is disabled, as it must be for voice.
"""

import json
import re
import sys
import time

import httpx
from common import gpu_mem_mb, load_llm_cases, save_result, summarize

OLLAMA = "http://localhost:11434"
NUM_CTX = 6144


def chat(model: str, messages: list[dict], tools: list[dict]) -> dict:
    """Stream a chat turn and return text, tool calls, and wall-clock timings."""
    body = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "stream": True,
        "think": False,
        "keep_alive": "30m",
        "options": {"num_ctx": NUM_CTX, "temperature": 0.2},
    }
    t0 = time.perf_counter()
    first = None
    text, calls, final = [], [], {}
    with httpx.stream("POST", f"{OLLAMA}/api/chat", json=body, timeout=120) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            msg = chunk.get("message", {})
            if (msg.get("content") or msg.get("tool_calls")) and first is None:
                first = time.perf_counter() - t0
            text.append(msg.get("content") or "")
            calls.extend(msg.get("tool_calls") or [])
            if chunk.get("done"):
                final = chunk
    total = time.perf_counter() - t0
    n_tok = final.get("eval_count", 0)
    gen_s = total - (first or 0.0)
    return {
        "text": "".join(text).strip(),
        "calls": [
            {"name": c["function"]["name"], "args": c["function"].get("arguments", {})}
            for c in calls
        ],
        "ttft_s": first if first is not None else total,
        "total_s": total,
        "tok_s": n_tok / gen_s if n_tok > 8 and gen_s > 0.05 else 0.0,
    }


def match(matcher: dict, value) -> bool:
    v = str(value)
    if "equals" in matcher:
        return v.strip().lower() == matcher["equals"].lower()
    if "contains_any" in matcher:
        return any(s.lower() in v.lower() for s in matcher["contains_any"])
    if "digits" in matcher:
        return re.sub(r"\D", "", v) == matcher["digits"]
    return False


def score_tool(expect: dict, res: dict) -> tuple[bool, str]:
    wanted = expect["calls"]
    allowed = {c["name"] for c in wanted}
    stray = [c["name"] for c in res["calls"] if c["name"] not in allowed]
    if stray:
        return False, f"unexpected tool {stray}"
    for w in wanted:
        ok = any(
            c["name"] == w["name"]
            and all(match(m, c["args"].get(k, "")) for k, m in w["args"].items())
            for c in res["calls"]
        )
        if not ok:
            return False, f"missing {w['name']} {list(w['args'])} got {res['calls']}"
    return True, ""


def score_text(expect: dict, res: dict) -> tuple[bool, str]:
    text = res["text"].lower()
    forbidden = set(expect.get("forbid_tools", []))
    if not expect.get("allow_tools", False) and res["calls"] and not expect.get("forbid_tools"):
        return False, f"called tools {[c['name'] for c in res['calls']]}"
    if any(c["name"] in forbidden for c in res["calls"]):
        return False, f"forbidden tool {[c['name'] for c in res['calls']]}"
    if expect.get("allow_tools") and res["calls"] and not text:
        return True, ""  # deferring to a callback or handoff is acceptable
    if not text:
        return False, "empty reply"
    for group in expect.get("all_of", []):
        if not any(s in text for s in group):
            return False, f"missing one of {group}"
    if expect.get("any_of") and not any(s in text for s in expect["any_of"]):
        return False, f"missing any of {expect['any_of'][:4]}..."
    if expect.get("must_not_regex") and re.search(expect["must_not_regex"], text):
        return False, "matched forbidden pattern"
    if len(text.split()) > expect.get("max_words", 60):
        return False, f"too long ({len(text.split())} words)"
    return True, ""


def run_model(model: str, repeats: int) -> dict:
    cfg = load_llm_cases()
    for _ in range(30):  # wait for any previous model to unload so the VRAM delta is clean
        if gpu_mem_mb() < 1800:
            break
        time.sleep(1)
    mem0 = gpu_mem_mb()
    chat(model, [{"role": "user", "content": "hi"}], cfg["tools"])  # load + warm
    vram = gpu_mem_mb() - mem0
    per_case, ttft, tok_s, words = {}, [], [], []
    for case in cfg["cases"]:
        passes, notes = 0, []
        for _ in range(repeats):
            res = chat(
                model,
                [{"role": "system", "content": cfg["system"]}, *case["messages"]],
                cfg["tools"],
            )
            ok, why = (score_tool if case["expect"]["kind"] == "tool" else score_text)(
                case["expect"], res
            )
            passes += ok
            if not ok:
                notes.append(why)
            ttft.append(res["ttft_s"])
            if res["tok_s"]:
                tok_s.append(res["tok_s"])
            if res["text"]:
                words.append(len(res["text"].split()))
        per_case[case["id"]] = {
            "category": case["category"],
            "pass": passes,
            "of": repeats,
            "fails": notes[:2],
        }
    total = sum(c["pass"] for c in per_case.values())
    cats: dict[str, list[int]] = {}
    for c in per_case.values():
        cats.setdefault(c["category"], [0, 0])
        cats[c["category"]][0] += c["pass"]
        cats[c["category"]][1] += c["of"]
    return {
        "model": model,
        "vram_mb": vram,
        "pass_rate": round(total / (len(per_case) * repeats), 3),
        "by_category": {k: round(a / b, 2) for k, (a, b) in cats.items()},
        "ttft_s": summarize(ttft),
        "tok_s_mean": round(sum(tok_s) / max(len(tok_s), 1), 1),
        "reply_words_mean": round(sum(words) / max(len(words), 1), 1),
        "cases": per_case,
    }


def main() -> None:
    args = sys.argv[1:]
    repeats = 3
    if "--repeats" in args:
        i = args.index("--repeats")
        repeats = int(args[i + 1])
        del args[i : i + 2]
    results = []
    for model in args:
        print(f"== {model}", flush=True)
        r = run_model(model, repeats)
        results.append(r)
        print(f"  pass {r['pass_rate']:.0%}  by category {r['by_category']}")
        print(
            f"  TTFT p50 {r['ttft_s']['p50']:.2f}s p95 {r['ttft_s']['p95']:.2f}s  {r['tok_s_mean']} tok/s  reply {r['reply_words_mean']} words  VRAM {r['vram_mb']} MB"
        )
        for cid, c in r["cases"].items():
            if c["pass"] < c["of"]:
                print(f"  - {cid} [{c['category']}] {c['pass']}/{c['of']}: {c['fails']}")
        subprocess_unload(model)
    save_result("llm", {"runs": results})


def subprocess_unload(model: str) -> None:
    httpx.post(f"{OLLAMA}/api/generate", json={"model": model, "keep_alive": 0}, timeout=30)


if __name__ == "__main__":
    main()
