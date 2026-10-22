"""TTS benchmark for Kokoro (kokoro-onnx): time to first audio chunk and real-time factor.

Run: uv run python evals/bench/bench_tts.py [--voices af_heart,bf_emma]
Measures time-to-first-audio on agent-style replies of three lengths, three times each.
"""

import asyncio
import sys
import time

from common import KOKORO_MODEL, KOKORO_VOICES, gpu_mem_mb, save_result, summarize
from kokoro_onnx import Kokoro

TEXTS = {
    "short": "Sure, I can help with that. Which trip are you asking about?",
    "medium": "The Goa Beach Getaway is a six day, five night package that includes airport transfers, daily breakfast and two guided tours. Flights are not included.",
    "long": (
        "The Kerala Backwaters Escape starts in Kochi and runs for seven days. You will spend two nights on a "
        "houseboat in Alleppey, visit a tea estate in Munnar, and finish with a day of free time on the beach "
        "at Kovalam. The price starts at twenty two thousand rupees per person and includes all transfers and breakfast."
    ),
}


async def first_chunk_and_total(
    kokoro: Kokoro, text: str, voice: str, lang: str
) -> tuple[float, float, float]:
    t0 = time.perf_counter()
    first, audio_s = None, 0.0
    async for samples, sr in kokoro.create_stream(text, voice=voice, speed=1.0, lang=lang):
        if first is None:
            first = time.perf_counter() - t0
        audio_s += len(samples) / sr
    total = time.perf_counter() - t0
    return first or total, total, audio_s


async def main() -> None:
    voices = ["af_heart", "bf_emma", "am_michael"]
    if "--voices" in sys.argv:
        voices = sys.argv[sys.argv.index("--voices") + 1].split(",")
    mem0 = gpu_mem_mb()
    kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
    await first_chunk_and_total(kokoro, "Warm up.", voices[0], "en-us")
    out = {"engine": "kokoro-onnx (CPU)", "vram_delta_mb": gpu_mem_mb() - mem0, "voices": {}}
    for voice in voices:
        lang = "en-gb" if voice.startswith("b") else "en-us"
        per_len = {}
        for name, text in TEXTS.items():
            ttfa, totals, rtfs = [], [], []
            for _ in range(3):
                f, t, a = await first_chunk_and_total(kokoro, text, voice, lang)
                ttfa.append(f)
                totals.append(t)
                rtfs.append(t / a)
            per_len[name] = {
                "ttfa_s": summarize(ttfa),
                "total_s": summarize(totals),
                "rtf": round(sum(rtfs) / len(rtfs), 3),
            }
            print(
                f"{voice:11s} {name:6s} TTFA {per_len[name]['ttfa_s']['p50']:.3f}s  total {per_len[name]['total_s']['p50']:.2f}s  RTF {per_len[name]['rtf']:.2f}",
                flush=True,
            )
        out["voices"][voice] = per_len
    save_result("tts", out)


if __name__ == "__main__":
    asyncio.run(main())
