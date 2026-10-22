# 02 — Stack research (SOTA, as of 2026-10-22)

> **Update 2026-10-22:** scope is **English only first**, so sections 3 (STT) and 5 (TTS) stand for now: faster-whisper large-v3-turbo and Kokoro. Kokoro has no Indian-English voice; svara-TTS is the alternative if that matters (see `05-indic-voice-stack.md`, which is the deferred Hindi/Telugu reference for phase 8). Section 7 (CRM): the CRM is self-hosted and Twenty is no longer assumed; see `00-requirements.md` question A.

How to read this: a web survey, mostly vendor and blog sources. Few of them benchmark voice-agent latency on a 12 GB card, so treat every number as directional. The **Recommendation** rows are choices for your hardware. Phase 5 of the plan validates them on your machine.

## 1. Orchestration framework

| Option | License | Strengths | Weaknesses | Fit |
|---|---|---|---|---|
| **Pipecat** | BSD-2 | Python pipeline, about 25 STT / 25 LLM / 40 TTS integrations, local options (Whisper, Moonshine, Ollama, Kokoro, Piper), SmallWebRTC transport (no server needed), Twilio/Telnyx/Plivo serializers, Smart Turn, Pipecat Flows for scripted conversations | Telephony is serializer-based, not a SIP stack | **Recommended** |
| LiveKit Agents | Apache-2.0 | Strongest at scale and native SIP telephony | Needs a LiveKit server, room/participant model has a steeper learning curve | Revisit if you need production telephony |
| Kyutai Unmute | MIT | Very low latency, own STT and TTS | Needs 16 GB or more VRAM, no native tool calling | Doesn't fit 12 GB or the CRM tool use |
| TEN Framework, Bolna, Vocode, FastRTC | various | Niche or prototype-oriented | | Not needed |

Pipecat wins because it is the only one that gives local components, a no-server browser transport and a path to telephony in one codebase. It also has a close reference to copy from: [kwindla/macos-local-voice-agents](https://github.com/kwindla/macos-local-voice-agents) (Silero VAD, Smart Turn v2, Whisper, a small Gemma, Kokoro, under 800 ms on a Mac). The Linux/CUDA version of that stack is essentially this project.

Requires Python 3.11 or later; 3.12 recommended (you have 3.12.3).

## 2. Voice activity and turn detection

| Layer | Pick | Notes |
|---|---|---|
| VAD | **Silero VAD** | Tiny, CPU, standard in Pipecat. |
| End-of-turn | **Smart Turn v3** (`LocalSmartTurnAnalyzerV3`) | About 8M params, int8 CPU build is about 8 MB, about 10–65 ms inference on CPU, 23 languages including Hindi, BSD-2. Decides whether a pause is the end of a thought, which cuts both interruptions and dead air versus a fixed silence timeout. |

## 3. Speech-to-text

| Model | Streaming | Quality / latency claims | VRAM | Notes |
|---|---|---|---|---|
| **faster-whisper large-v3-turbo** | Chunked, not native | About 7.75% WER, 99 languages | about 1.5–2 GB (int8) | **Recommended start.** Mature, best language coverage, and Pipecat's Whisper service works directly. With VAD-segmented utterances the "not streaming" weakness mostly disappears because you transcribe a complete short utterance. |
| NVIDIA Parakeet TDT 0.6B v3 | Chunked | About 6.3% mean WER, very high throughput, CC-BY-4.0 | about 2 GB | Best accuracy per millisecond on NVIDIA. Needs NeMo (heavy dependency). Try if Whisper latency disappoints. |
| Moonshine v2 (Tiny/Small/Medium) | Native streaming | Paper reports 50 / 148 / 258 ms; Medium 6.65% WER | under 1 GB | Lowest latency, but English-focused. Good fallback if VRAM is tight. |
| Kyutai STT (1B en/fr, 2.6B en) | Native streaming | 0.5 s / 2.5 s delay | 2.5 GB+ | English and French only. |
| NVIDIA Nemotron ASR streaming | Native, 80–1120 ms chunks | Per one guide | | Newer, unverified. Watch, don't start with it. |

If your callers speak Indian-language or code-mixed speech, Whisper is the only one of these with credible coverage. Please confirm languages (open question 1).

## 4. LLM

Hard constraint: it must share 12 GB with STT and TTS, and it must call tools reliably.

| Model | Size at Q4 | Notes |
|---|---|---|
| **Gemma 4 E4B** (`gemma4:latest`, already pulled) | 9.5 GB on disk (8B total params, about 4B effective) | Native function calling per Google. The "about 4 GB" figure in the sources is the effective-parameter size, not the file. Smaller QAT build: `gemma4:e4b-it-qat`, 6.1 GB. **Start here, but measure VRAM in phase 1b.** |
| **Qwen3.5-9B** | about 6 GB | Cited as the pick for 8–16 GB cards (BFCL-V4 66.1, vendor-reported). Roughly 90 tok/s measured on a 4080. **Recommended challenger.** |
| Gemma 4 `e2b` | 4.6 GB | Smaller Gemma 4 fallback if E4B does not leave room. |
| Qwen3.6 27B / 35B-A3B, GLM-4.7-Flash | 18–22 GB | The popular "24 GB" picks. Do not fit. |
| Llama 3.2 3B | about 2 GB | Fast, but weak at multi-step tool use. Fallback only. |

Serving: **Ollama** to start (already installed, OpenAI-compatible, simple). Move to **vLLM** only if you need better concurrency or constrained tool-call decoding; it needs more VRAM headroom and tuning.

Voice-specific LLM rules (these matter more than the model choice):

- Short outputs: system prompt demands one or two spoken sentences, no markdown, no lists.
- Few tools with narrow, flat schemas. Four to six tools at most.
- Disable "thinking" mode on reasoning-capable models. It adds seconds of silence.
- Stream tokens; Pipecat flushes to TTS at sentence boundaries.

## 5. Text-to-speech

| Model | Params | Latency (reported) | Cloning | License | Notes |
|---|---|---|---|---|---|
| **Kokoro** | 82M | About 45 ms first chunk on an RTX 5090; about 100 ms on an M4; also CPU-capable | No (preset voices) | Apache-2.0 | **Recommended.** Light, fast, good quality, native Pipecat service, and also served by Speaches. |
| Orpheus 3B | 3B | About 200 ms claimed | Limited | Apache-2.0 | More expressive, much heavier. It would crowd the LLM out of 12 GB. |
| CosyVoice2-0.5B | 0.5B | About 150 ms claimed (marketing figure) | Yes | Apache-2.0 | Strong candidate if you need a brand voice. Unverified. |
| Fish Audio S2 | | Streaming, high Arena rank | Yes | check license | Quality leader candidates; check weights license for commercial use. |
| Kyutai TTS 1.6B / Pocket TTS | 1.6B / small | About 750 ms on an L40S (Unmute) | Voice repo | MIT | 1.6B too heavy. Pocket TTS (242 MB) is worth a look. |
| Piper | tiny | Independent benchmark: 1.7 s on CPU | No | MIT | Not conversational-latency. Skip. |
| Chatterbox | | Very slow on CPU | Yes | MIT | Needs a GPU. Skip for now. |

Start with Kokoro. If voice character matters for sales (warmth, a specific persona), do a bake-off in phase 5 against CosyVoice2 and Fish S2, watching VRAM.

## 6. Knowledge / RAG

| Layer | Pick | Why |
|---|---|---|
| Chunking | Structure-aware (headings/FAQ pairs), 200–400 tokens, store source + title | Voice answers need short, precise passages. |
| Embeddings | **bge-m3** (1024-d, multilingual) on CPU to start; `Qwen3-Embedding-0.6B` as an alternative | The embedding step dominated retrieval latency in the one benchmark I found (about 300 ms of 340 ms), so run it locally, never via a remote API. |
| Vector store | **LanceDB** (embedded) or **Qdrant** (Docker) | A few thousand chunks fit in memory; embedded means no extra service. Postgres + pgvector is the right choice if the CRM database is already Postgres. |
| Reranker | Optional (`bge-reranker-v2-m3` on CPU) | Only add if recall is poor. It adds a round trip. |
| Pattern | Retrieval as an **LLM tool** (`search_knowledge`) rather than retrieving on every turn | Skips the cost for small talk and lets the model decide. Add a "prefetch on keyword" optimisation later if latency demands. |

## 7. CRM integration

Which CRM do you use? That decides everything here (open question 2). Options:

| CRM | Integration route | Notes |
|---|---|---|
| **HubSpot** (hosted) | Official MCP server (remote and local developer server, GA in early 2026) or the REST API | Works with the free CRM tier. Easiest if you're already on HubSpot. Data leaves the machine by design. |
| Salesforce, Pipedrive, Zoho, Attio | MCP servers (first-party for Attio, community for Pipedrive) or REST | Same pattern. |
| **Twenty** (open source, self-hostable) | REST + GraphQL API, API key from Settings > APIs & Webhooks; several community MCP servers | Best **local test CRM**: Docker, free, realistic contacts/companies/opportunities/notes/tasks. |
| EspoCRM, SuiteCRM | REST APIs | Older, workable. Not researched in depth. |

Integration rule (from the MCP/CRM sources): expose **a handful of named, safe actions**, not the raw CRM API. Your agent's tools should look like this and nothing more:

| Tool | Type | Confirmation |
|---|---|---|
| `lookup_contact(phone_or_email)` | read | none |
| `get_open_deals(contact_id)` | read | none |
| `create_lead(name, phone, email, interest, notes)` | write | read back details before saving |
| `log_call_note(contact_id, summary)` | write | none (end of call) |
| `schedule_callback(contact_id, when, reason)` | write | read back time |
| `handoff_to_human(reason)` | action | none |

Build the CRM layer behind a small adapter interface (`CRMClient`) with a `MockCRM` first, then `TwentyCRM`, then your real CRM. Tools call the interface, so the swap doesn't touch the agent. Using a CRM's MCP server directly inside Pipecat is possible but exposes far more tools than a voice LLM can handle; wrap it with an allow-list instead.

## 8. What I'd skip

- Speech-to-speech models (OpenAI Realtime, Gemini Live, Ultravox): hosted, not local.
- Kyutai Unmute as the base: no tool calling and 16 GB or more VRAM.
- 27B+ LLMs: do not fit.
- Voice cloning for v1: adds VRAM and legal questions for no testing value.

## Sources

- Frameworks: [AssemblyAI orchestration roundup](https://www.assemblyai.com/blog/orchestration-tools-ai-voice-agents), [Pipecat vs LiveKit (Fora Soft)](https://www.forasoft.com/blog/article/pipecat-vs-livekit-agents), [Soniox framework wiki](https://soniox.com/wiki/voice-agent-frameworks), [Pipecat repo](https://github.com/pipecat-ai/pipecat)
- Local reference build: [kwindla/macos-local-voice-agents](https://github.com/kwindla/macos-local-voice-agents), [WebRTC.ventures open-source voice agent](https://webrtc.ventures/2026/02/building-an-open-source-voice-ai-agent-that-avoids-vendor-lock-in/)
- Turn detection: [pipecat-ai/smart-turn](https://github.com/pipecat-ai/smart-turn)
- STT: [Northflank STT benchmarks](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks), [Moonshine v2 paper](https://arxiv.org/pdf/2602.12241), [Moonshine vs Whisper](https://modelslab.com/blog/audio-generation/moonshine-vs-whisper-asr-real-time-speech-2026), [ASR beyond Whisper](https://builderai.tools/blog/speech-recognition-beyond-whisper-parakeet-sensevoice), [Gladia roundup](https://www.gladia.io/blog/best-open-source-speech-to-text-models)
- TTS: [Picovoice on-device TTS benchmark](https://picovoice.ai/blog/on-device-tts/), [Pinggy self-hosted TTS guide](https://pinggy.io/blog/best_open_source_self_hosted_text_to_speech_models/), [Orpheus vs Kokoro](https://codersera.com/blog/orpheus-3b-vs-kokoro-tts-comparison-of-open-source-ai-voice-synthesis-models/amp/), [dTelecom Kokoro latency](https://blog.dtelecom.org/we-replaced-elevenlabs-with-kokoro-tts-on-an-m4-gpu-latency-fell-to-100-ms-and-tts-cost-nearly-68bcc3313cdd)
- Speech server: [speaches-ai/speaches](https://github.com/speaches-ai/speaches); Unmute: [kyutai-labs/unmute](https://github.com/kyutai-labs/unmute)
- LLM: [Local LLMs for function calling](https://insiderllm.com/guides/function-calling-local-llms/), [Best local LLM for agents](https://vdf.ai/blog/best-local-llm-for-agents-tool-calling/), [Gemma 4 E4B on RTX 4070](https://modelfit.io/can-i-run/gemma4-e4b-q4-on-rtx-4070/), [Gemma 4 E4B vLLM W4A16](https://huggingface.co/pearsonkyle/gemma4-e4b-w4a16-mtp-vLLM)
- RAG: [Voice AI vector DB latency](https://growwstacks.com/blog/fastest-rag-solution-voice-ai-agents/), [vector DB comparison 2026](https://techsy.io/en/blog/best-vector-databases-2026)
- CRM/MCP: [Best MCP servers for CRM](https://www.fast.io/resources/best-mcp-servers-crm.md), [HubSpot MCP](https://peliqan.io/blog/hubspot-mcp/), [Twenty MCP servers (community)](https://glama.ai/mcp/servers/Jdu278/twenty-mcp-server)
