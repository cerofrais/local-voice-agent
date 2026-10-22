# 05 — Hindi / Telugu / Indian-English voice stack

> **Deferred (phase 8).** Scope is English only first. This file is the research for adding Hindi and Telugu later. The only part relevant now is the TTS table, if you want an Indian-English voice (svara-TTS).

_2026-10-22. This supersedes the STT and TTS recommendations in `02-stack-research.md` wherever they conflict. Evidence here is thinner than for English: most sources are vendor blogs and model cards, and I found **no verified Telugu word-error-rate comparison and no verified streaming latency for any Indic TTS**. So the plan makes a language spike (phase 1b) the first thing to do, before building anything else on top._

## 1. Why this is the hard part

- English voice stacks are mature. Indic ones are not: fewer models, less benchmarking, weaker streaming support.
- Phone-quality audio makes it worse. One vendor source puts Hindi WER at roughly 12–18% on clean speech and 22–30% on telephony audio, with Telugu likely worse.
- Callers code-mix (Hindi/Telugu sentences with English words such as trip and place names). Models trained on one language at a time stumble on this.
- Names matter here: trip names, destinations, people's names and phone numbers must be captured exactly.

## 2. Speech-to-text candidates

| Model | Hindi | Telugu | Streaming | Size / license | Notes |
|---|---|---|---|---|---|
| **AI4Bharat IndicConformer 600M** (multilingual) | Yes (13.2 WER on the Vaani Hindi benchmark, per model card) | Yes | Not stated on the card; assume utterance-level | About 600M params, MIT, gated download | Best-supported open model for 22 Indian languages. The card now points to a newer **Indic-Transcribe** (25 languages, claimed better), which I haven't evaluated. Python via `transformers` with `trust_remote_code`; ONNX runtime paths exist. |
| **Whisper large-v3 / large-v3-turbo** | Usable | Weaker | Chunked | MIT | Strong for Indian-accented English and code-mixed English words. Telugu is its weak spot. Fine-tuned Telugu/Hindi Whisper models exist (e.g. from Project Vaani), no WER found. Supports an `initial_prompt` to bias toward trip and place names. |
| IndicWhisper | Reported about 13.8% avg Hindi WER (vendor-authored source) | Likely | Chunked | Whisper fine-tune | Worth including in the bake-off. |
| Moonshine, Parakeet, Kyutai STT | English-centric | No | Varies | | Not suitable for the Hindi/Telugu requirement. |

**Plan:** run a bake-off on your own audio (phase 1b) between IndicConformer (and Indic-Transcribe if it fits), Whisper large-v3-turbo, and a Telugu Whisper fine-tune. Probable outcome: Whisper for English plus an Indic model for Hindi/Telugu, routed by the language the caller chose. Because all of these work on finished utterances (VAD plus Smart Turn decide when the caller is done), lack of native streaming costs a few hundred milliseconds, not a rebuild.

**Language selection:** ask at the start ("English, हिंदी, or తెలుగు?") or detect from the first utterance, then confirm. Don't rely on silent auto-detection for short utterances; it's unreliable on code-mixed speech. Allow a mid-call switch.

**Names and numbers:** capture phone numbers and spellings by reading back and asking for confirmation. Prefer the caller giving digits in small groups. Bias recognition with the trip catalog (Whisper prompt) and fuzzy-match against it afterwards.

## 3. Text-to-speech candidates

| Model | English (Indian) | Hindi | Telugu | Size | Streaming / latency | License |
|---|---|---|---|---|---|---|
| **svara-TTS v1** (Kenpath) | **Yes, Indian English** | Yes | Yes | Orpheus-style, about 3B, 19 languages, trained on about 2000 h of open Indic speech | Orpheus family claims about 200 ms streaming, but I found no svara-specific numbers or serving recipe. Card mentions GGUF/vLLM compatibility for a sibling model. | Apache-2.0 per an aggregator; **verify on the model card** |
| **Indic Parler-TTS** (AI4Bharat) | Yes (English) | Yes | Yes | About 938M; wrappers estimate 3–4 GB VRAM or 4–6 GB RAM | Not designed for streaming. A wrapper's sample shows around 2.4 s, which is not a benchmark but is a warning | Apache-2.0, gated |
| **IndicF5** (AI4Bharat) | Likely | Yes | Yes | Voice-cloning style, 11 languages | No latency data | check card |
| AI4Bharat Indic-TTS (older) | | Yes | Yes | VITS-style, light | Likely fast | MOS about 3.6–3.9; fine for informational lines, weaker for sales warmth |
| **Kokoro** | **No** (US/UK only) | 4 voices, graded C | **No** | 82M | Excellent | Apache-2.0 |
| Piper, MMS-TTS | | | | Light | Fast | I could not confirm Telugu/Hindi voice catalogs or quality. Candidates for a CPU fallback only. |
| Sarvam Bulbul | | Yes | Yes | | | **API-only**, so not local. Ruled out by the data policy. |

**Plan:** the front-runner is **svara-TTS** (the only one covering Indian English, Hindi and Telugu in a single model with an LLM-style streaming design). The spike must measure its time-to-first-audio on the 4070 *while the LLM is also generating*. Both are autoregressive token generators sharing one GPU, which is the main latency risk. Fallbacks, in order: Indic Parler-TTS (slower, sentence-level), then Kokoro for English plus Hindi only, then a lighter VITS-style model.

**Pronunciation/script:** the LLM should answer in the caller's script (Devanagari for Hindi, Telugu script for Telugu) and keep proper nouns in a form the TTS reads correctly. Numbers, dates and prices need spoken-form normalization per language (e.g. "twelve thousand five hundred rupees" in the chosen language). Build a small text normalizer and test it.

## 4. LLM for three languages

- Evidence: one preprint (L3Cube IndicQuest v2) found Gemma 4 31B ahead of Sarvam 30B on Indic factual questions, strongest in Hindi. That is a 31B model, not the 4B-class model that fits your GPU. Qwen3.5 is claimed to cover 200+ languages. Neither claim is a measured Telugu result for a small model.
- Open Sarvam weights (Sarvam-1) are base models for fine-tuning, not drop-in chat models. The commercial Sarvam models are API-only.
- **Plan:** test Gemma 4 E4B and Qwen3.5-9B on a Telugu/Hindi/English scenario set (lead-field extraction, trip disambiguation, short answers from retrieved text). If Telugu is poor, options are: (a) a bigger local model if a GPU upgrade is acceptable, (b) the **hosted-LLM fallback** for the dialog while STT/TTS stay local (allowed under your policy, with PII masking), (c) have the LLM reason in English internally and translate only the spoken reply, at a latency cost.
- Keep the LLM's job narrow: slot-filling and answering from retrieved passages. Narrow tasks tolerate smaller models.

## 5. GPU budget with the Indic stack (revises `01`)

All figures are estimates.

| Component | Where | VRAM |
|---|---|---|
| LLM (Gemma 4 E4B Q4, 4–6k context) | GPU | 4.5–5.5 GB |
| svara-TTS (about 3B, Q4/GGUF or similar) | GPU | 2.5–4 GB |
| IndicConformer 600M | GPU or CPU | 1.5–2 GB on GPU |
| Whisper large-v3-turbo (int8) | GPU or CPU | 1.5–2 GB on GPU |
| Silero, Smart Turn v3, embeddings | CPU | 0 |

Everything on the GPU totals about **10–13 GB: over the limit**. Practical configurations:

- **A (preferred to test first):** LLM + TTS on GPU (about 7–10 GB), both STT models on **CPU** (the i7-14700K has 28 threads, and utterances are short). Needs measurement; CPU decoding of a 600M Conformer could add a second or more.
- **B:** one STT model on GPU (the Indic one), Whisper on CPU for English only.
- **C:** hosted-LLM fallback enabled: the local LLM is unloaded, which frees about 5 GB for the speech models. This keeps audio local but sends masked text out.
- **D:** buy or rent a 24 GB GPU. If A and B both miss latency targets, this is the honest answer, and it also makes a 9B+ LLM comfortable.

## 6. Telephony notes (Plivo, later)

- Pipecat ships a `PlivoFrameSerializer` for Plivo's audio-streaming WebSocket (8 kHz mu-law), with an XML webhook that opens the stream to your server.
- Using Plivo means call audio passes through the carrier and reaches your machine over a public tunnel. That's inherent to telephony, and your "fully local" rule should be read as "no cloud AI services", which I'm assuming. Tell me if the rule is stricter.
- Indian numbers and outbound calling have regulatory steps (number KYC, DLT registration for commercial outbound). I did not research these; check with Plivo before planning outbound.
- Re-run the STT bake-off on 8 kHz telephone-band audio before committing. Models that look fine on studio audio can degrade a lot.

## 7. What I could not verify

- Telugu WER for any model on call-quality audio.
- Whether IndicConformer or Indic-Transcribe can stream, and their CPU/GPU speed for short utterances.
- svara-TTS latency, VRAM use, license text, and how to serve it in streaming mode.
- Gemma 4 E4B and Qwen3.5-9B quality in Telugu.
- Plivo India region specifics.

Each of these has an exit criterion in phase 1b of the plan.

## Sources

- [IndicConformer 600M model card](https://huggingface.co/ai4bharat/indic-conformer-600m-multilingual)
- [Whisper vs IndicWhisper vs Shunya (vendor blog)](https://www.shunyalabs.ai/blog/whisper-vs-indicwhisper-vs-shunya-a677)
- [Open-source voice AI for India (Caller Digital, vendor blog)](https://caller.digital/blog/open-source-voice-ai-india-sarvam-ai4bharat-bhasini-2026)
- [Open-source speech models, Project Vaani](https://vaani.iisc.ac.in/models)
- [Indic ASR factors study](https://arxiv.org/pdf/2606.09335), [TTS-STT flywheel / entity-dense Indic ASR](https://arxiv.org/pdf/2605.03073)
- [svara-TTS v1](https://huggingface.co/kenpath/svara-tts-v1), [svara-TTS voice-clone beta](https://huggingface.co/kenpath/svara-tts-voiceclone-beta)
- [Indic Parler-TTS overview](https://dev.co/ai/llms/indic-parler-tts), [vexyl-tts wrapper](https://github.com/vexyl-ai/vexyl-tts), [AI4Bharat IndicF5](https://github.com/AI4Bharat/IndicF5), [AI4Bharat Indic-TTS](https://github.com/AI4Bharat/Indic-TTS)
- [Kokoro voices](https://huggingface.co/hexgrad/Kokoro-82M/blob/main/VOICES.md)
- [IndicQuest v2 (Gemma 4 vs Sarvam)](https://arxiv.org/pdf/2608.15535), [Gemma 4 vs Qwen 3.5](https://codersera.com/blog/gemma-4-vs-qwen-3-5-comparison-2026/)
- [Pipecat Plivo serializer](https://docs.pipecat.ai/server/services/serializers/plivo), [Pipecat Plivo WebSockets guide](https://docs.pipecat.ai/pipecat/telephony/plivo-websockets)
- Self-hosted CRMs: [selfhost.dev comparison](https://selfhost.dev/blog/best-open-source-crm-2026/), [Twenty vs EspoCRM](https://use-apify.com/blog/twenty-crm-vs-espocrm-2026)
