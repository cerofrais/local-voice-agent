# 04 — Implementation plan

Each phase ends with something you can run and judge. Do them in order; every later phase depends on the earlier ones. Time estimates assume part-time work and are rough.

## Phase 0 — Environment (half a day)

- [ ] Reboot to clear the NVIDIA driver/library mismatch. Confirm `nvidia-smi` shows the RTX 4070 SUPER with about 12 GB.
- [ ] Install `uv`, `espeak-ng`, `ffmpeg`, `libsndfile1`.
- [ ] Models: `gemma4:latest` (= E4B) is present. `qwen3.5:9b` needs Ollama newer than 0.23.2 (the pull returns HTTP 412), so upgrade Ollama first. Set `OLLAMA_KEEP_ALIVE=-1`.
- [ ] Free disk space if needed (52 GB free; models need about 25 GB).
- [ ] Clone the empty repo, add `pyproject.toml`, `.env.example`, and the docs.

**Done when:** `ollama run <model>` answers on GPU and `nvidia-smi` shows it loaded.

## Phase 1b — English speech spike (1–2 days) — do this before phase 1

**Status: done 2026-10-22.** Results and the chosen models are in [06-phase1b-results.md](06-phase1b-results.md): Whisper large-v3-turbo (GPU), Gemma 4 E4B, Kokoro on GPU; about 0.62 s mean for STT + LLM + TTS, measured. Still open: real Indian-accented recordings, and svara-TTS if an Indian-English voice matters.

Scope is English only, with Indian-accented callers. This is a short measurement pass to pick the models, not a build.

- [ ] Test set: 10–20 real call recordings if you have them, plus about 30 generated English utterances with trip names, places, dates, phone numbers and a few Indian-accented samples. Include an 8 kHz copy of each for the later Plivo work.
- [ ] **STT:** faster-whisper large-v3-turbo vs large-v3 (int8_float16). Measure WER, exact-match on names and digits, latency, VRAM. Try the Whisper `initial_prompt` with trip names.
- [ ] **TTS:** Kokoro (default, but no Indian-English voice) vs svara-TTS (has Indian English, heavier) vs Indic Parler-TTS. Measure time-to-first-audio on the 4070 **while Ollama is generating**, VRAM, and have someone score how natural the voice sounds. If Kokoro is acceptable, take it; the Indian-accent voice is a nice-to-have, not a requirement.
- [ ] **LLM:** Gemma 4 E4B vs Qwen3.5-9B on a scripted set: extract lead fields, pick the right trip, answer from a supplied passage, refuse out-of-scope.

**Done when:** a table of measured latency, VRAM and accuracy exists and one STT, TTS and LLM combination is chosen that fits in 12 GB with 1.5 GB spare and meets about 1.2 s voice-to-voice in a quick end-to-end test.

## Phase 1 — Baseline voice loop, no tools (2–3 days)

**Status: done 2026-10-23.** `src/agent/bot.py` runs browser WebRTC, Silero VAD, Smart Turn v3, Whisper large-v3-turbo, Gemma 4 E4B and Kokoro on the GPU. Measured with an automated test call: first bot audio about 0.9–1.0 s after the caller's last word; VRAM 7.9 GB and flat across calls. Details in [06-phase1b-results.md](06-phase1b-results.md). Not yet tried with a human voice and a real microphone.

(English only. Run after phase 1b, using the chosen models.)

- [ ] Pipecat pipeline: SmallWebRTC, Silero VAD, Smart Turn v3, faster-whisper, Ollama (OpenAI-compatible), Kokoro.
- [ ] A short persona prompt tuned for speech.
- [ ] Per-stage timing logs (endpoint, STT, LLM first token, TTS first audio, total).
- [ ] Barge-in works.

**Done when:** you can talk to it in the browser, voice-to-voice latency is measured, and VRAM stays under about 10.5 GB while a conversation runs. Target under 1.2 s typical. If you miss it, apply the tuning list in section "Latency tuning" below before moving on.

## Phase 2 — Knowledge (2–3 days)

- [ ] Collect real source material: FAQs, product sheets, pricing rules, policies. **You provide these**; the agent is only as good as they are.
- [ ] Ingestion CLI: parse, heading-aware chunk, embed with bge-m3, store in LanceDB.
- [ ] Check whether the PDFs are text or scans (OCR if scans) and how price/date tables extract.
- [ ] Build the **trip catalog** from the PDFs (structured records with English names and aliases; Hindi/Telugu aliases come in phase 8) and the `identify_trip` tool; review the extracted catalog by hand, since errors here become wrong answers on calls.
- [ ] Crawl the website and help center into the same index; keep a source URL per chunk.
- [ ] `search_knowledge` tool; answers cite their source in logs. (Phase 8: Hindi/Telugu queries should retrieve English passages; bge-m3 is multilingual, verify then.)
- [ ] RAG eval set of 30–50 questions with expected sources.

**Done when:** at least 90% of eval questions retrieve the right chunk in the top 3, and the agent says "I don't have that" instead of guessing on out-of-scope questions. Retrieval adds under 150 ms.

## Phase 3 — CRM integration (3–4 days)

The full integration design, field mapping, proposed API and test plan are in [07-crm-integration.md](07-crm-integration.md); the steps below follow its section 9.

- [ ] Get the lead details sheet columns (`00-requirements.md` question B) and write `LeadSchema` from them, with the question wording for each field in English (store wording per language so phase 8 only adds translations).
- [ ] `LeadForm` state tracking, `set_lead_field` / `get_missing_fields` / `submit_lead` tools, spell-and-confirm routines for names, phone and email.
- [ ] `CRMClient` interface plus `MockCRM` and contract tests.
- [ ] Tools: lookup, open deals, create lead, log note, schedule callback, handoff. Validation and idempotency in code.
- [ ] Read-back-then-confirm flow for writes.
- [ ] Adapter for **your self-hosted CRM** (product to be confirmed, question A) running in Docker with seeded sample data, passing the same contract tests. Until it's named, use Twenty or EspoCRM as the stand-in; the adapter interface keeps the swap cheap.
- [ ] Test against a copy of the CRM, never live data.

**Done when:** a spoken conversation creates a lead with correct fields in the CRM, a second call recognises the same caller, and a duplicate attempt is rejected.

## Phase 4 — Persona, flows, guardrails (2–3 days)

- [ ] Separate prompts for support and sales modes.
- [ ] Pipecat Flows for identity capture, qualification and confirmation only.
- [ ] Guardrails: out-of-scope refusal, no invented prices, human-handoff phrases, silence and no-input handling, "repeat that" handling.
- [ ] Prompt-injection tests (a CRM note saying "ignore your instructions...").
- [ ] End-of-call summary written to the CRM as a note.

**Done when:** the scripted scenarios in Phase 5 pass without manual steering.

## Phase 5 — Evaluation and tuning (3–5 days)

- [ ] **Scenario suite**: 15–25 scripted calls (happy path, interruptions, accents, noisy audio, unknown questions, angry caller, changes of mind). Generate caller audio with TTS or record it; replay through the pipeline.
- [ ] **Latency harness**: p50/p95 voice-to-voice over the suite.
- [ ] **Tool-call accuracy**: right tool, right arguments, no missing confirmation.
- [ ] **Model bake-offs** on your hardware: Gemma 4 E4B vs Qwen3.5-9B; faster-whisper vs Parakeet vs Moonshine; Kokoro vs CosyVoice2 vs Fish S2 if voice quality matters.
- [ ] Replace estimates in `docs/01` and `docs/02` with measured numbers.

**Done when:** you have a table of measured latency, tool accuracy and RAG accuracy per model combination, and a chosen default.

### Latency tuning, in order of payoff
1. Keep every model resident (no cold loads). `OLLAMA_KEEP_ALIVE=-1`.
2. Smaller or faster LLM; shorter system prompt; cap `num_ctx` to what you need (4–8k).
3. Disable thinking mode. Cap reply length.
4. Tune the Smart Turn stop threshold and VAD silence window.
5. Sentence-level streaming into TTS; start TTS on the first clause.
6. Move STT to a lighter model, or embeddings to a faster one.
7. Warm up each model with a dummy request at startup.

## Phase 6 — Telephony (optional, 2–4 days)

- [ ] Plivo number and account (check India number rules and, for outbound, DLT registration), tunnel (ngrok or Cloudflare) to the local bot, Pipecat `PlivoFrameSerializer` (8 kHz mu-law WebSocket).
- [ ] Re-run the phase 1b STT bake-off on real telephone audio before trusting accuracy numbers.
- [ ] 8 kHz mulaw audio path: check STT accuracy on phone-quality audio.
- [ ] Inbound first, outbound only with consent and legal review.
- [ ] Decide whether the carrier hop is acceptable for "local".

**Done when:** you can call a number and complete a scenario from Phase 5.

## Phase 7 — Hardening (as needed)

- Retention and deletion policy for transcripts.
- Auth on any exposed endpoint; secrets in `.env`, never in the repo.
- Crash recovery and a health check; model warm-up on start.
- Decide the production target: bigger GPU, hosted LLM fallback, LiveKit for scale.

## Phase 8 — Hindi and Telugu expansion (deferred; start only after the English milestone works)

Hindi and Telugu are the largest unknowns. Begin with a measurement-only spike (no agent logic), then wire the language router, Hindi/Telugu prompts, lead-field wording and trip aliases. Background and candidates are in `05-indic-voice-stack.md`.

- [ ] Build a small test set: 10–20 real call recordings if you have them (`00-requirements.md` question F), plus about 30 generated utterances per language covering trip names, places, dates, phone numbers, and code-mixed sentences. Include an 8 kHz telephone-band copy of each.
- [ ] **STT bake-off:** IndicConformer 600M (and Indic-Transcribe if available), Whisper large-v3-turbo, a Telugu Whisper fine-tune. Measure WER on Hindi, Telugu, Indian English, plus exact-match accuracy on names and digits, plus latency on GPU and on CPU.
- [ ] **TTS bake-off:** svara-TTS, Indic Parler-TTS, Kokoro (English and Hindi only). Measure time-to-first-audio on the 4070 **while Ollama is also generating**, VRAM, and have a native speaker score naturalness and pronunciation (1–5).
- [ ] **LLM check:** Gemma 4 E4B vs Qwen3.5-9B on a scripted set in each language: extract lead fields, pick the right trip, answer from a supplied passage, refuse out-of-scope.
- [ ] Choose a GPU placement from `05-indic-voice-stack.md` section 5 (A/B/C/D) from measurements.

**Done when (spike):** a table with measured numbers exists, and for each language we have a chosen STT, TTS and LLM that meets: Hindi/Telugu name+digit accuracy at least 85% on clean audio, TTS time-to-first-audio under about 700 ms with the LLM busy, and the whole set fitting in VRAM with 1 GB spare. If Telugu misses, decide between a hosted-LLM fallback, an English-reasoning detour, or a bigger GPU **before** building more.

## Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Small LLM calls tools unreliably | Medium–high | Few simple tools, code-side validation, confirmation step, eval suite, try Qwen3.5-9B. |
| VRAM exhaustion with all models loaded | Medium | Budget in `docs/01`, move STT or TTS to CPU, smaller quant. |
| Latency over 1.5 s | Medium | Tuning list above; Moonshine or lighter LLM. |
| Poor STT on accents, phone audio or code-mixed speech | Medium | Test early with real recordings (Phase 5); Whisper has the widest coverage. |
| **(Phase 8) Telugu/Hindi STT or TTS not good enough locally** | **High** | Start phase 8 with its measurement spike; model routing per language; hosted-LLM fallback does not fix speech, so the real fallbacks are a bigger GPU or a hosted speech service (which breaks the local rule and needs your explicit OK). |
| **TTS and LLM contend for one GPU, so latency rises** | **High** | Measure in phase 1b with both active; STT on CPU; smaller LLM; or a second/larger GPU. |
| Wrong trip identified, or wrong lead field captured (misheard names, digits) | Medium–high | Trip resolver with confirmation; spell-and-confirm; read back before submit; catalog-biased recognition. |
| Knowledge base thin or messy | High | It is your input. Curate before Phase 2. |
| Hallucinated commitments (price, discount) | Medium | Answer-only-from-KB rule, no pricing tool that can promise, human handoff. |
| Legal/consent for AI calls and recording | Certain to apply | Disclosure at call start; legal review before real customers. |
| Dependency churn (Pipecat moves fast) | Medium | Pin versions in `pyproject.toml`, upgrade deliberately. |

## Open questions — answered 2026-10-22

Answers and the new questions (A–H) are in [00-requirements.md](00-requirements.md). The original list is kept below for reference.

1. **Languages**: English only, or Hindi / Telugu / code-mixed? This decides the STT and TTS choice (Kokoro is strongest in English; Whisper covers the most languages).
2. **Which CRM** do you actually use (HubSpot, Zoho, Salesforce, something custom)? Hosted or self-hosted?
3. **Channel**: browser only for the test, or do you need phone calls (inbound/outbound)?
4. **Use case priority**: support, sales qualification, or both? What are the first three things the agent must do?
5. **Knowledge**: what do you have today (PDFs, website, FAQ sheet, help center), and roughly how big?
6. **Voice**: is a generic good voice fine, or does it need a specific brand voice (cloning)?
7. **Data policy**: is fully local a hard requirement, or can a hosted LLM be a fallback if the local one falls short?
