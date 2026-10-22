# 03 — Architecture

## 1. Overview

```
 Browser (mic/speaker)                                   Docker / local services
 ┌──────────────┐  WebRTC (SmallWebRTC)   ┌─────────────────────────────────────┐
 │ test client  │ ◄─────────────────────► │ Pipecat bot (Python process)        │
 └──────────────┘                         │                                     │
                                          │ audio in → Silero VAD               │
   (later) Twilio/Telnyx ── WebSocket ──► │         → Smart Turn v3             │
                                          │         → STT (faster-whisper) ─┐   │
                                          │                                 ▼   │
                                          │   context + system prompt ► LLM ────┼──► Ollama :11434 (/v1)
                                          │                              │ tool calls
                                          │        ┌─────────────────────┴───┐  │
                                          │        │ tools                   │  │
                                          │        │  search_knowledge ──────┼──┼──► Vector store + bge-m3
                                          │        │  crm_* ─ CRMClient ─────┼──┼──► MockCRM | Twenty | real CRM
                                          │        │  handoff_to_human       │  │
                                          │        └─────────────────────────┘  │
                                          │ LLM text (sentences) → Kokoro TTS   │
                                          │ audio out ◄─────────────────────────┘
                                          │ call log / transcript / metrics → SQLite + files
                                          └─────────────────────────────────────┘
```

One process owns the real-time loop. Everything heavy is either in-process (STT, TTS, VAD, turn detection) or behind a local HTTP API (Ollama, CRM, vector store).

## 2. Components

| Component | Choice | Runs on | Interface |
|---|---|---|---|
| Orchestrator | Pipecat | CPU | Python |
| Transport | SmallWebRTC (browser), Twilio WebSocket serializer later | CPU | WebRTC / WS |
| VAD | Silero | CPU | in-process |
| Turn detection | Smart Turn v3 | CPU | in-process |
| STT | faster-whisper large-v3-turbo, int8_float16 | GPU | in-process (or Speaches over HTTP) |
| LLM | Gemma 4 E4B or Qwen3.5-9B via Ollama | GPU | OpenAI-compatible HTTP |
| TTS | Kokoro | GPU or CPU | in-process (or Speaches) |
| Knowledge | LanceDB + bge-m3 | CPU | in-process library |
| CRM | `CRMClient` adapter: Mock, Twenty, real | Docker / remote | HTTP |
| Observability | structured logs, per-stage timings, call transcript | CPU | SQLite and JSONL |

In-process STT/TTS is simplest for v1. Moving them behind Speaches (OpenAI-compatible server) is a possible later refactor if you want to restart the bot without reloading models.

### Revisions from the requirements (2026-10-22)

_English-only first: build with a single language, but keep `language` a config value passed to the STT, TTS and prompt layers so phase 8 can add routing without a rewrite. The `LanguageRouter` and Hindi/Telugu aliases below are phase 8. The trip resolver, lead form and PII-masked fallback are needed now._

- **STT and TTS are language-routed (phase 8).** A `LanguageRouter` holds the caller's chosen language (`en`, `hi`, `te`) and selects the STT model, TTS voice and prompt language for each turn. Candidates are in `05-indic-voice-stack.md`; Kokoro in the table above is English-only and no longer the default.
- **Trip resolver.** A `TripCatalog` is built from the PDFs at ingestion time (one record per trip: id, name, aliases in all three languages, destination, duration, departure dates, price, inclusions, source file). The `identify_trip(description)` tool does hybrid search (embedding plus fuzzy string match on names and aliases) and returns up to three candidates with scores. The agent must confirm with the caller ("Is it the 7-day Kerala backwaters trip?") before locking it in; if scores are close it asks a clarifying question. STT output is also fuzzy-matched against the catalog to repair misheard place names.
- **Lead form.** The lead details sheet is turned into a schema (`LeadSchema`: field name, type, required, validation, how to ask it in each language). A `LeadForm` state object tracks which fields are filled and confirmed. Tools: `set_lead_field(name, value)` (validated in code), `get_missing_fields()`, `submit_lead()` (only when all required fields are confirmed, then read back). The LLM asks for one missing field at a time, and the conversation ends only when required fields are done or the caller declines.
- **Phone and email capture** use a spell-and-confirm routine, with digits read back in small groups in the caller's language.
- **Hosted-LLM fallback** is a config-switched alternate `LLMService` behind a PII-masking layer (see `00-requirements.md` section 4). Off by default.

## 3. Turn lifecycle

1. Caller audio streams in. Silero marks speech start/stop.
2. On a pause, Smart Turn v3 decides whether the turn is complete. If not, keep listening. If yes, finalise the transcript.
3. Transcript is appended to the conversation context. The LLM streams a reply. If it needs facts, it calls `search_knowledge`; if it needs customer data, it calls a CRM tool.
4. Tool results go back to the LLM, which continues. A short filler ("one moment") may be spoken while a slow tool runs.
5. Text is flushed to Kokoro sentence by sentence, so audio begins before the LLM finishes.
6. **Barge-in**: if the caller starts speaking during playback, output is cancelled and the context records what was actually spoken.
7. At call end, a summary is written to the CRM as a note, and the full transcript is stored locally.

## 4. Agent behaviour design

### System prompt contents
- Role and persona (company name, tone, name of the agent).
- Hard rules: answer only from `search_knowledge` results or CRM data; if not found say so and offer a callback or human handoff; never invent prices, dates or policies; speak in one or two short sentences; no markdown, lists or URLs read aloud.
- Call goals by mode (support: resolve or escalate; sales: qualify, then book a callback).
- Disclosure: tell the caller it is an automated assistant, and that the call may be recorded if you record.

### Conversation structure
Start with a free-form agent plus tools. Add **Pipecat Flows** (explicit states) only for the parts that must be deterministic: identity capture, qualification questions, and the confirm-then-write step. A full state machine from day one makes the agent robotic.

### Tool policy
- Reads execute immediately. Writes read back the data and wait for an explicit yes.
- Every write has an idempotency key (call id + action) so a retry can't create a duplicate lead.
- Tool arguments are validated in code (phone/email formats, date parsing) before the CRM sees them. The LLM never writes raw API calls.
- Tool results returned to the LLM are trimmed to the few fields needed, to keep the context short and the model focused.

## 5. Knowledge pipeline

```
docs/ (md, pdf, html, faq csv) → parse → chunk (heading-aware) → embed (bge-m3) → LanceDB
                                                                         ▲
                       search_knowledge(query) → top 3–4 chunks + source ┘
```

- Ingestion is a CLI (`python -m agent.knowledge.ingest ./knowledge`), re-runnable and idempotent.
- Each chunk keeps source path and title, so the LLM can say where an answer came from and you can audit it.
- Evaluation set: 30–50 real questions with expected source chunks, run on every change (recall at k and answer faithfulness).

## 6. CRM adapter

```python
class CRMClient(Protocol):
    async def lookup_contact(self, phone: str | None, email: str | None) -> Contact | None: ...
    async def get_open_deals(self, contact_id: str) -> list[Deal]: ...
    async def create_lead(self, lead: LeadIn, idempotency_key: str) -> Lead: ...
    async def log_note(self, contact_id: str, text: str, idempotency_key: str) -> None: ...
    async def schedule_callback(self, contact_id: str, when: datetime, reason: str) -> Task: ...
```

Implementations: `MockCRM` (in-memory + JSON, for tests), `TwentyCRM` (local Docker), then the real one. Contract tests run against any implementation so the swap is mechanical.

## 7. Data, privacy and safety

- Audio is not stored by default. Transcripts and tool calls are stored locally in SQLite; add retention and deletion.
- Everything stays on the machine except the optional telephony carrier hop and a hosted CRM, if that is what you use.
- Consent and disclosure: recording and AI-agent disclosure rules vary by country (India, EU and US states differ). Decide with your counsel before real customers are on calls. Test calls with your own team first.
- Prompt-injection surface: CRM notes and knowledge documents are untrusted text that goes into the LLM context. Tools must be limited so that even a hijacked model can only do what the allow-list permits (no delete, no bulk export, no send-email).
- Handoff: `handoff_to_human` always works, and a spoken "talk to a person" triggers it.

## 8. Proposed repo layout

```
local-voice-agent/
├── README.md
├── docs/                      # these documents
├── pyproject.toml             # uv-managed, Python 3.12
├── docker-compose.yml         # Twenty CRM (+ Postgres, Redis), optional Qdrant
├── .env.example
├── src/agent/
│   ├── bot.py                 # Pipecat pipeline assembly
│   ├── config.py              # settings (models, URLs, thresholds)
│   ├── prompts/               # system prompts per mode (support, sales)
│   ├── tools/                 # tool schemas + handlers
│   ├── crm/                   # base.py, mock.py, twenty.py
│   ├── knowledge/             # ingest.py, search.py
│   └── observability/         # timings, transcript logging
├── knowledge/                 # your source documents (gitignored if private)
├── evals/
│   ├── latency/               # scripted-audio latency harness
│   ├── tools/                 # tool-call accuracy cases
│   └── rag/                   # question set with expected sources
└── tests/
```
