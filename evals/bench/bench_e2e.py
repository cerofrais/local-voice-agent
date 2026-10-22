"""End-to-end voice-to-voice latency with all three models resident (no VAD or endpointing).

Per utterance: Whisper transcribes the finished audio, the LLM streams a reply, and Kokoro speaks the
first sentence while the LLM keeps generating (so the GPU and CPU are contended as in a real call).
Reported time = STT + LLM time to first full sentence + TTS time to first audio. Add roughly
250-400 ms for end-of-turn detection to estimate what the caller experiences.

Run: uv run python evals/bench/bench_e2e.py --stt large-v3-turbo:cuda:int8_float16 --llm gemma4:latest
"""

import argparse
import asyncio
import json
import re
import time

import httpx
from common import (
    AUDIO_DIR,
    KOKORO_MODEL,
    KOKORO_VOICES,
    gpu_mem_mb,
    load_llm_cases,
    load_utterances,
    read_wav,
    save_result,
    summarize,
)
from faster_whisper import WhisperModel
from kokoro_onnx import Kokoro

OLLAMA = "http://localhost:11434"
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Utterances that should get a spoken reply rather than a tool call.
QUESTION_IDS = ["u03", "u09", "u10", "u14", "u15", "u24", "u25", "u27", "u30", "u17"]


def tts_first_audio(kokoro: Kokoro, text: str) -> float:
    async def go() -> float:
        t0 = time.perf_counter()
        async for _samples, _sr in kokoro.create_stream(
            text, voice="af_heart", speed=1.0, lang="en-us"
        ):
            return time.perf_counter() - t0
        return time.perf_counter() - t0

    return asyncio.run(go())


async def stream_turn(model: str, messages: list[dict], tools: list[dict], on_sentence) -> dict:
    body = {
        "model": model,
        "messages": messages,
        "tools": tools,
        "stream": True,
        "think": False,
        "keep_alive": "30m",
        "options": {"num_ctx": 6144, "temperature": 0.2},
    }
    t0 = time.perf_counter()
    buf, first_token, sent_at, tts_task, calls = "", None, None, None, []
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream("POST", f"{OLLAMA}/api/chat", json=body) as r:
            async for line in r.aiter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                msg = chunk.get("message", {})
                calls.extend(msg.get("tool_calls") or [])
                piece = msg.get("content", "")
                if piece:
                    if first_token is None:
                        first_token = time.perf_counter() - t0
                    buf += piece
                    m = SENTENCE_END.search(buf)
                    if sent_at is None and m and m.start() > 15:
                        sent_at = time.perf_counter() - t0
                        tts_task = asyncio.create_task(
                            asyncio.to_thread(on_sentence, buf[: m.start() + 1])
                        )
                if chunk.get("done"):
                    break
    done_at = time.perf_counter() - t0
    if tts_task is None and buf.strip():  # reply was a single sentence
        sent_at = done_at
        tts_task = asyncio.create_task(asyncio.to_thread(on_sentence, buf.strip()))
    ttfa = await tts_task if tts_task else 0.0
    return {
        "text": buf.strip(),
        "calls": calls,
        "sentence_s": sent_at or done_at,
        "done_s": done_at,
        "tts_ttfa_s": ttfa,
    }


async def llm_first_sentence(
    model: str, system: str, user: str, tools: list[dict], on_sentence
) -> dict:
    """Speak the first sentence of the reply. A tool call costs an extra LLM turn (stub result)."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    r = await stream_turn(model, messages, tools, on_sentence)
    if r["calls"] and not r["text"]:
        messages += [
            {"role": "assistant", "content": "", "tool_calls": r["calls"]},
            {
                "role": "tool",
                "tool_name": r["calls"][0]["function"]["name"],
                "content": "No matching information found.",
            },
        ]
        second = await stream_turn(model, messages, tools, on_sentence)
        second["sentence_s"] += r["done_s"]
        second["tool_turn"] = r["calls"][0]["function"]["name"]
        r = second
    r["reply"] = r["text"]
    return r


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stt", default="large-v3-turbo:cuda:int8_float16")
    ap.add_argument("--llm", default="gemma4:latest")
    ap.add_argument("--condition", default="phone", choices=["clean", "noisy", "phone"])
    ap.add_argument("--rounds", type=int, default=2)
    a = ap.parse_args()

    mem0 = gpu_mem_mb()
    name, device, compute = a.stt.split(":")[:3]
    stt = WhisperModel(name, device=device, compute_type=compute)
    kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
    cfg = load_llm_cases()
    system, tools = cfg["system"], cfg["tools"]
    # Warm everything once.
    list(stt.transcribe(read_wav(AUDIO_DIR / "clean" / "u01.wav"), language="en", beam_size=1)[0])
    await llm_first_sentence(a.llm, system, "Hello", tools, lambda s: tts_first_audio(kokoro, s))
    vram = gpu_mem_mb() - mem0

    rows = []
    for _ in range(a.rounds):
        for uid in QUESTION_IDS:
            audio = read_wav(AUDIO_DIR / a.condition / f"{uid}.wav")
            t0 = time.perf_counter()
            segs, _ = stt.transcribe(
                audio, language="en", beam_size=1, condition_on_previous_text=False
            )
            text = " ".join(s.text.strip() for s in segs)
            stt_s = time.perf_counter() - t0
            r = await llm_first_sentence(
                a.llm, system, text, tools, lambda s: tts_first_audio(kokoro, s)
            )
            r["empty"] = not r["text"]
            r["stt_s"] = stt_s
            r["total_s"] = stt_s + r["sentence_s"] + r["tts_ttfa_s"]
            r["transcript"] = text
            rows.append(r)
            print(
                f"{uid} stt {stt_s:.2f}  llm1st {r['sentence_s']:.2f}  tts {r['tts_ttfa_s']:.2f}  = {r['total_s']:.2f}s  | {r.get('tool_turn', '')} {r['reply'][:60]}",
                flush=True,
            )

    out = {
        "stt": a.stt,
        "llm": a.llm,
        "condition": a.condition,
        "vram_all_models_mb": vram,
        "stt_s": summarize([r["stt_s"] for r in rows]),
        "llm_first_sentence_s": summarize([r["sentence_s"] for r in rows]),
        "tts_ttfa_s": summarize([r["tts_ttfa_s"] for r in rows]),
        "total_s": summarize([r["total_s"] for r in rows]),
        "empty_replies": sum(r["empty"] for r in rows),
        "tool_turns": sum("tool_turn" in r for r in rows),
        "rows": rows,
    }
    print(
        f"\nVRAM with all models loaded: {vram} MB (Ollama model included only if already resident)"
    )
    print("total (excl. endpointing):", out["total_s"])
    save_result(f"e2e_{a.llm.replace(':', '_')}", out)


if __name__ == "__main__":
    asyncio.run(main())
