# 06 — Phase 1b results: English model bake-off

_Run 2026-10-22 on harsha-pc-ubuntu (RTX 4070 SUPER 12 GB, i7-14700K). Harness: `evals/bench/` (run everything with `evals/bench/run_all.sh`); raw results in `evals/results/`._

## Decision

| Stage | Choice | Why |
|---|---|---|
| STT | faster-whisper **large-v3-turbo**, `int8_float16`, on the GPU, with the trip-name `initial_prompt` | 6% WER, 0.1 s per utterance, 1.2 GB. The bigger large-v3 is no more accurate, twice as slow and 50% heavier. On the CPU it takes about 3 s per utterance, which is too slow. |
| LLM | **Gemma 4 E4B** (`gemma4:latest`), thinking off, tools supplied | 90% on the 20-case suite, about 126 tok/s, 4.5 GB. Qwen3.5-9B does not emit tool calls through Ollama (see below). |
| TTS | **Kokoro on the GPU** (onnxruntime-gpu), voice `af_heart` | 60–150 ms to first audio. On the CPU it was 0.5–1.1 s, which would have been the largest latency cost. No Indian-English voice. |

**Voice-to-voice, measured:** STT + LLM first sentence + TTS first audio = **0.62 s mean, 0.74 s p95** (Gemma, phone-band audio, 20 turns including tool-call turns). Add about 0.25–0.4 s for end-of-turn detection, so a caller should wait roughly **0.9–1.1 s**. The target was under 1.2 s typical, so it is met with some room. VAD, Smart Turn and WebRTC are not included yet.

**GPU memory with everything loaded (absolute, nvidia-smi):** 1.1 GB desktop baseline + Whisper 1.3 GB + Kokoro 0.5 GB + Gemma 4.5 GB = **7.4 GB used, 4.8 GB free**. With Qwen it would be 9.5 GB (2.7 GB free). Context is 6144 tokens.

## What the numbers don't prove

- The test audio is **synthetic** (Kokoro voices, US/UK only). Real Indian-accented callers on a phone line will do worse, and noise at a realistic level was only mildly harmful here. Treat the STT numbers as a ceiling. Drop real recordings into `evals/real/` (`name.wav` 16 kHz mono + `name.txt`) and re-run `bench_stt.py`.
- 30 spoken sentences, 20 LLM cases, 3 repeats, one run each. Differences of a few points are noise.
- Trip names are placeholders; the real catalog changes the entity tests.
- The end-to-end test uses a stub tool result and no knowledge base, and does not include VAD or Smart Turn.
- The Indian-English voice question is still open: svara-TTS was not tested.

## STT (30 utterances x 3 conditions)

Entity accuracy = share of key items (trip names, phone numbers, emails, dates) recovered. `noisy` is 6 dB SNR; `phone` is 8 kHz band-limited mu-law.

| Config | WER clean / noisy / phone | Entities clean / noisy / phone | Latency p50 | VRAM |
|---|---|---|---|---|
| turbo, GPU int8_float16 | 6.2% / 8.2% / 6.2% | 0.91 / 0.80 / 0.91 | 0.10 s | 1.2 GB |
| turbo, GPU, **with trip-name prompt** | 6.7% / 8.0% / 6.5% | **0.93 / 0.83 / 0.93** | 0.10 s | 1.0 GB |
| large-v3, GPU int8_float16 | 6.5% / 8.7% / 6.5% | 0.91 / 0.78 / 0.89 | 0.20 s | 1.8 GB |
| turbo, CPU int8 | 6.2% / 8.2% / 6.5% | 0.91 / 0.80 / 0.89 | 2.95 s | 0 |

Recurring mistakes (they are the same on every config):
- **Person names**: "Raghunathan" became "ragonathan" or "Ragnathan". The prompt only biases trip names.
- Trip names: "Family Fun Package" heard as "Family Fund" without the prompt.
- "lakh" heard as "lack"; emails arrive as text like `harsha.v at example.com`.

Implication: names and emails need **spell-and-confirm**, trip names should be fuzzy-matched against the catalog, and email and phone values need **normalisation in code**, not by the LLM.

## LLM (20 cases x 3 runs, thinking off, with tool rules in the system prompt)

| | Gemma 4 E4B | Qwen3.5-9B |
|---|---|---|
| Overall pass | **90%** | 60% |
| Extract lead fields | 94% | 17% |
| Trip identification / disambiguation | 83% | 75% |
| Knowledge, grounded answers, injection, out-of-scope | 100% | 100% |
| Handoff, callback | 100% | 100% / 0% |
| Flow (asks the next field) | 50% | 50% |
| Time to first token p50 / p95 | 0.19 / 0.35 s | 0.20 / 0.65 s |
| Speed | about 126 tok/s | about 77 tok/s |
| Reply length | 19 words | 22 words |

Findings:
- **Qwen3.5-9B via Ollama does not produce tool calls**. In the failing cases it answers in prose ("I have noted your name") without calling `set_lead_field`, which is dangerous because it claims to have saved data it did not. It happens with thinking on or off. This could be an Ollama parser or template issue for this model; it is not a verdict on the model. Revisit with a newer Ollama or vLLM only if Gemma proves insufficient.
- **The system prompt must list explicit tool rules.** Before adding them, Gemma scored 82% and never called `handoff_to_human` or (mostly) `schedule_callback`; both models asked clarifying questions instead.
- **Gemma returns empty replies if the prompt talks about tools but none are supplied** (16 of 60 plain turns), and 0 of 24 when tools are supplied or the prompt is short. Always send the tool list, and still treat an empty reply as an error to retry.
- Gemma's remaining misses: it passes the spoken email text "priya dot raghunathan at gmail dot com" straight through (code must normalise), sometimes skips the confirmation question after `identify_trip`, and does not always ask for the next missing field. These are prompt and flow issues for phase 4 (use Pipecat Flows for the lead form so the next field is not left to the model).
- Both models passed all prompt-injection, out-of-scope and grounded-answer cases.

## TTS: Kokoro time to first audio

| Text | CPU (onnxruntime) | GPU (onnxruntime-gpu) |
|---|---|---|
| Short sentence (about 3 s of audio) | 0.49–0.58 s | **0.06 s** |
| One long sentence (about 8 s) | 1.1–1.6 s | **0.13–0.15 s** |
| Two sentences (about 13 s) | 2.7–3.2 s | **0.28–0.33 s** |

Kokoro does not start playing before a whole text chunk is synthesised, so the agent should still send the **first sentence** to TTS as soon as the LLM completes it.

## Things that went wrong on the way (worth knowing)

- faster-whisper needs the CUDA libraries (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`). They are now dependencies; `evals/bench/common.py` preloads them. The app needs the same loader.
- Plain `onnxruntime` is CPU-only. `pyproject.toml` now excludes it and installs `onnxruntime-gpu[cuda,cudnn]` instead (it ships CUDA 13 libraries, which driver 580 supports).
- Hugging Face downloads can stall silently; setting `HF_HUB_DOWNLOAD_TIMEOUT=30` and re-running resumes them.
- The first run showed noisy and phone audio scoring identically to clean, which meant the noise was too mild. It is now 6 dB SNR.

## Next

1. Re-run with **real Indian-accented recordings** when you have them.
2. Optionally test **svara-TTS** if the Indian-English voice matters.
3. Build the live pipeline (phase 1): Pipecat, Silero VAD, Smart Turn v3, these three models, browser WebRTC; then re-measure real voice-to-voice latency.
