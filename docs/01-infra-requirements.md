# 01 — Infra requirements

_Last updated: 2026-10-22. Figures marked **(est.)** are my estimates from model sizes and vendor/blog claims, not measurements on this machine. Phase 5 of the plan replaces them with measured numbers._

> **Update 2026-10-22:** scope is **English only first** (see `00-requirements.md`), so the English GPU budget in section 3 below stands. Hindi/Telugu (phase 8) would need the larger budget in `05-indic-voice-stack.md` section 5: those models don't all fit in 12 GB beside the LLM. If you choose an Indian-English TTS (svara-TTS) for the English phase, use that file's TTS numbers instead of Kokoro's.

## 1. What you have today

Inspected on the dev machine:

| Resource | Value | Verdict |
|---|---|---|
| GPU | NVIDIA RTX 4070 SUPER, **12 GB VRAM** | The binding constraint. Everything below is sized around it. |
| CPU | Intel i7-14700K, 28 threads | Plenty. Run VAD, turn detection, embeddings and the orchestrator on CPU. |
| RAM | 62 GB (about 48 GB available) | Plenty. Allows CPU offload and a large vector index. |
| Disk | 328 GB total, **52 GB free** (84% used) | Enough for models (about 25 GB) but watch it. |
| OS / runtimes | Ubuntu 24.04, Python 3.12.3, Docker 29.4.3, Ollama installed, Node 24 | Good. `uv` is not installed. |
| Models already pulled | `gemma4:latest` (9.6 GB) | Too big to share the GPU with STT and TTS. See section 3. |
| Repo | `cerofrais/local-voice-agent` exists on GitHub, **empty** (0 bytes, `main`) | Greenfield. |

### Blocker to fix first: NVIDIA driver/library mismatch

`nvidia-smi` fails with `Failed to initialize NVML: Driver/library version mismatch`. The loaded kernel module is 580.173.02 and the userspace library is 580.178. This usually means the driver packages were upgraded without a reboot. Reboot, then confirm `nvidia-smi` shows the 4070 SUPER. Nothing GPU-related can be benchmarked until this works.

> **Measured full-stack GPU use (phase 1b):** 7.4 GB of 12 GB with desktop 1.1 + Whisper turbo 1.3 + Kokoro on GPU 0.5 + Gemma 4 E4B 4.5, leaving 4.8 GB. See `06-phase1b-results.md`.

## 2. Target: what "good enough to test" means

The goal is a local rig that can hold a believable support/sales conversation, answer from supplied knowledge, and read and write the CRM.

| Metric | Target | Why |
|---|---|---|
| Voice-to-voice latency (user stops talking to first audio) | **under 1.0 s typical, under 1.5 s p95** | Beyond about 1.2 s the conversation feels broken. The macOS Pipecat reference reports under 800 ms on a fully local stack, which is the proof it's reachable. |
| Concurrent calls | **1** (maybe 2 with a smaller LLM) | This is a test rig. A single 12 GB card is not a production call center. |
| Knowledge-grounded answers | Answers only from the KB, otherwise says it doesn't know and offers a handoff | Prevents invented prices and policies. |
| CRM actions | Lookup, create lead, log note, schedule callback, with read-back confirmation before writes | |
| Fully local | No audio, transcript or CRM data leaves the machine | The point of the exercise. |

### Latency budget (est.)

| Stage | Budget |
|---|---|
| Endpointing (VAD silence + Smart Turn decision) | 250–400 ms |
| STT final transcript | 100–250 ms |
| RAG retrieval (embed + search) when needed | 30–100 ms |
| LLM time-to-first-token, plus first sentence | 200–400 ms |
| TTS time-to-first-audio | 80–200 ms |
| Network/WebRTC (localhost) | under 30 ms |
| **Total** | **about 0.7–1.4 s** |

Two rules keep this achievable: stream everything (LLM tokens go to TTS sentence by sentence), and keep the models resident in VRAM so there are no cold loads mid-call.

## 3. GPU memory budget (12 GB)

Everything that is always loaded must fit with headroom for KV cache and CUDA overhead. Keep at least 1.5 GB free.

| Component | Placement | VRAM (est.) |
|---|---|---|
| LLM, 4–9B class at Q4 | GPU | 4.5–7 GB incl. KV cache at 4–8k context |
| STT: faster-whisper large-v3-turbo (int8_float16) | GPU | about 1.5–2 GB |
| TTS: Kokoro 82M | GPU (or CPU) | about 0.5–1 GB |
| VAD: Silero | CPU | 0 |
| Turn detection: Smart Turn v3 (int8, about 8 MB) | CPU | 0 |
| Embeddings (bge-m3 or similar) | CPU to start | 0 (about 1.2 GB if moved to GPU) |
| Reranker (optional) | CPU | 0 |
| **Total on GPU** | | **about 7–10 GB** |

Consequences:

- **Correction (phase 0):** the already-pulled `gemma4:latest` *is* Gemma 4 E4B (Ollama lists `latest` and `e4b` with identical sizes): 8.0B total parameters at Q4_K_M, 9.5 GB on disk. "E4B" refers to about 4B *effective* parameters, not file size. My earlier "about 4 GB" figure was wrong. **Measured 2026-10-22 on the 4070 SUPER:** `ollama ps` reports 100% GPU, and total GPU memory in use is **5.6 GB** at `num_ctx=6144`, so about 6.7 GB of the 12 GB is left for STT and TTS. Warm speed is about **118 tok/s with 20 ms to first token** when thinking is off (`"think": false`). The first call took 25 s to load and was slow because it was cold and thinking was on, so keep the model resident and always disable thinking. **`qwen3.5:9b` measured the same way:** 7.7 GB total GPU memory (about 4.5 GB left for speech), about 77 tok/s, 40 ms warm to first token (3 s cold). So Gemma is lighter and about 50% faster; Qwen's case rests on tool-call and answer quality, which phase 1b tests. Other candidates on Ollama: `gemma4:e4b-it-qat` (6.1 GB), `gemma4:e2b` (4.6 GB), `qwen3.5:9b` (6.6 GB), `qwen3.5:4b` (3.3 GB). `gemma4:12b` also exists but is larger still.
- Dense or MoE models of 27–35B, which the "best local LLM for 24 GB" guides recommend, do not fit. Don't copy those recommendations.
- If the GPU gets tight, move STT to CPU (`int8`, slower) before shrinking the LLM, or move TTS to CPU. Kokoro is light enough to run on CPU.
- Set `OLLAMA_KEEP_ALIVE=-1` so the LLM is never unloaded between turns.

## 4. Software prerequisites

| Item | Needed for | Status |
|---|---|---|
| NVIDIA driver + CUDA runtime | GPU inference | Mismatch, reboot required |
| `uv` (Python env manager) | Reproducible Python 3.12 env | Install |
| Docker + Compose | CRM, vector DB, optional speech server | Installed |
| Ollama | LLM serving (OpenAI-compatible API at `:11434/v1`) | Installed |
| espeak-ng, ffmpeg, libsndfile | Kokoro phonemizer and audio I/O | Install via apt |
| A modern browser with mic access | WebRTC test client | Available |
| Headset or echo-cancelling mic | Avoids the agent hearing itself | Recommended |

## 5. Network and external needs

- **Browser testing**: localhost only. No public endpoint needed.
- **Telephony (optional, later)**: Twilio/Telnyx need a publicly reachable webhook and media WebSocket (ngrok or Cloudflare Tunnel), plus a paid number. Audio would transit the carrier, so "fully local" no longer strictly holds for phone calls. See the plan, phase 6.
- **Model downloads**: roughly 15–25 GB once. After that it can run offline.

## 6. When this machine stops being enough

- More than 1–2 concurrent calls, a larger LLM for better tool use (27B+), or voice cloning TTS that needs 8 GB or more: move to a 24 GB card (RTX 3090/4090) or rent one for load tests.
- Expect the LLM to be the weakest link for multi-step tool use at 4–9B. If the eval in phase 5 shows unreliable tool calls, the cheapest fix is a better prompt and fewer, simpler tools, then a bigger GPU, then a hybrid with a hosted LLM (breaking the fully-local goal).
