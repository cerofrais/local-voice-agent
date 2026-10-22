# 07 — CRM integration

_2026-10-23. Written for two readers: whoever builds the voice agent (this repo) and whoever owns the CRM. It says what the agent needs from the CRM, what it will write back, and how the two are tested together. Machine-readable contract: [`crm-api.openapi.yaml`](crm-api.openapi.yaml)._

## 1. Status and honesty about what is known

| Item | State |
|---|---|
| Voice agent | Live loop works (phase 1). Tools, knowledge and CRM calls are not built yet. |
| Reference CRM | You pointed me at the "wishtrip CRM" demo (https://demo-travel-crm-eight.vercel.app, by Ascent-Intelligent-Technologies). I signed in as its read-only *Demo Viewer* role and read its screens. |
| Backend of the reference CRM | **None that I could see.** The login page says data is generated in the browser, and the page made no network calls I could observe. So there is no API to integrate with yet: **section 6 is a proposed contract** that the CRM must implement (or that we implement on top of whatever it has). |
| Target CRM | To be set up on harsha-pc-ubuntu. Whether it is this product's source, a build modelled on it, or another CRM is still open (section 11). |

Everything marked **observed** below comes from the demo's screens. Everything marked **proposed** is my design and needs the CRM owner's agreement.

## 2. What the reference CRM looks like (observed)

It is a CRM for travel consultants: Instagram-to-booking pipeline, itineraries, payments, travel documents. It already assumes a voice agent called **Aanya** ("calls new leads, answers FAQs, hands off anything sensitive") and a chat agent **Wish**, plus lead scoring, with a human review queue. Our agent plays Aanya's role.

**Main objects:** enquiries (leads, ids like `WT-1058`, internal `enq_004s`), travellers, trips, itineraries, payments, documents, visas, suppliers, tasks, calls, inbox threads, activity log.

**Pipeline stages (enquiry `stage`):** `new`, `contacted`, `qualified`, `itinerary_sent`, `negotiation`, `advance_paid`, `docs_visa`, `ready`, `travelling`, `completed`, `lost`, `non_lead`.

**Enquiry sources:** Instagram comment "PLAN", Instagram DM, Website planner, WhatsApp, Meta lead form, Google, Referral, Email, Phone call, Repeat traveller.

**Calls list** shows, per call: caller name and phone, linked enquiry (or "unlinked lead"), handled by (Aanya or a consultant), start time, duration, status, **outcome** (`qualified`, `callback`, `not_interested`, `booked`, `handoff`, `info`, `voicemail`, `no_answer`), sentiment, quality score, recording, direction (inbound/outbound), with transcripts.

**Agent guardrails the CRM already defines** (we must obey these in the voice agent too):
- Never quotes refunds or cancellation fees. Any cancellation, refund or dispute is escalated with an **urgent callback task**; the agent only acknowledges and hands off.
- Never promises availability or a confirmed price: it says "from ₹X per person" and "subject to availability". Confirmed numbers come from a consultant-sent itinerary.
- Escalates on cancellation, complaint, medical issues and visa rejection: tags the enquiry `needs-human` and flags it for attention.
- Respects quiet hours; human review of AI-built itineraries; payment links only from consultants.
- **PII:** passport numbers are masked (`••••1234`) in summaries, transcripts and agent events. Full numbers live only in the document vault.

## 3. The lead sheet (observed form, proposed required fields)

You described a "lead details sheet". The enquiry detail form in the demo looks like exactly that. **Please confirm this is the sheet, or send yours** (section 11, question 1). Fields as they appear:

### Traveller
| Field | Type / allowed values | Voice agent |
|---|---|---|
| `full_name` | text | **required** |
| `phone` | E.164 (`+91…`) | **required**; confirm the caller-ID number first, ask only if different |
| `email` | email | optional (spell-and-confirm) |
| `city` | text | optional |
| `instagram` | `@handle` | optional, rarely by voice |
| `preferred_channel` | `whatsapp` `instagram` `email` `sms` `phone` | ask once ("how would you like us to send the plan?") |

### Trip
| Field | Type / allowed values | Voice agent |
|---|---|---|
| `destination` | enum: `goa` `kerala` `dharamshala` `thailand` `japan` `vietnam` `bali` `malaysia` `dubai` `kashmir` `ladakh` `andaman` | **required**; resolved by the trip resolver |
| `alternatives` | free text list (e.g. "Bali, Vietnam") | optional |
| `travel_start`, `travel_end` | dates | one of dates or month is **required** |
| `month_if_flexible` | `YYYY-MM` | used when dates are not fixed |
| `nights` | integer | derived, or asked |
| `flexibility` | `fixed` `plus_minus_3` `flexible_month` | ask when dates are given |
| `adults`, `children`, `infants`, `rooms` | integers | **adults required**; children asked; infants and rooms optional |
| `trip_type` | `couple` `honeymoon` `family` `friends` `solo` `parents` `corporate` | **required** (often inferable) |
| `pace` | `slow` `balanced` `packed` | optional |
| `comfort` | `budget` `comfort` `premium` `luxury` | optional |
| `food` | `veg` `non_veg` `jain` `vegan` `halal` `no_preference` | optional |
| `budget_total_inr` | integer, rupees, whole group | **required**; the agent must handle "one and a half lakh" |
| `departure_city` | text | **required** |
| `include_flights` | boolean | optional |
| `interests` | tags: beaches, mountains, food, nightlife, culture, wildlife, adventure, wellness, shopping, photography, islands, road trip, temples, cafes, autumn colours, snow | optional, multi-select |
| `notes` | free text | anything else the caller says (anniversary, must-sees, things to avoid) |

### Set by the CRM or by the agent as metadata
| Field | Value for voice leads |
|---|---|
| `source` | `phone_call` |
| `stage` | **proposed:** `new` if required fields are missing, `qualified` when all required fields are confirmed and the caller wants a plan |
| `owner` | assigned by the CRM's routing rules; the agent never picks one |
| `tags` | the agent may add `needs-human`, `flexible-dates`, `discount-asked`, `honeymoon`, `parents` |
| `ai_score` | computed by the CRM |
| `created_by` | `aanya-voice` (identifies the agent in the audit trail) |

The "required" column is a **proposal**. The agent will not end a call as "qualified" until the required fields are set and read back.

## 4. How the agent uses the CRM during a call

```
caller dials / connects
  |
  v
1. lookup_contact(phone)  ---- found ---->  greet by name, load open enquiries
  |                                         ("Are you calling about your Kerala trip?")
  | not found
  v
2. identify destination / trip (identify_trip -> destination + candidates, caller confirms)
  v
3. collect fields one at a time (set_lead_field), validated in code, spell-and-confirm for names/email
  v
4. read back the summary  ->  caller says yes
  v
5. submit_lead()  ->  POST enquiry (idempotent)  ->  returns WT id, spoken as "your reference is W T ten fifty eight"
  v
6. hand-off topics at any point (cancellation, refund, complaint, medical, visa rejection, discount)
        -> create urgent callback task + tag needs-human, acknowledge, no quotes
  v
7. end of call: POST call record (outcome, transcript, duration, sentiment) linked to the enquiry
```

**Rules**
- **Read before write, confirm before write.** Reads run immediately; any write is preceded by a spoken read-back and a yes.
- **Idempotent writes.** Each write carries an `Idempotency-Key` (`<call_id>:<action>`), so a retry (network blip, double tool call) cannot create a duplicate lead.
- **Upsert, not blind create.** If the caller already has an open enquiry for the same destination in the last N days, the agent updates it (after confirming) instead of creating a second one.
- **Partial leads are saved.** If the caller hangs up half way, the agent still writes what it has (stage `new`, tag `incomplete-voice-lead`) plus the call record, so a consultant can call back. Collected fields are never lost.
- **No invented facts.** Prices are spoken only as "from ₹X per person, subject to availability", sourced from the knowledge base, never from memory.
- **PII masking in transcripts:** passport numbers and card-like digit strings are masked before the transcript is sent.
- **If the CRM is down:** the agent tells the caller a consultant will call back, writes the lead to a local outbox (`data/outbox/*.json`), and a retry worker replays it with the same idempotency keys.

### Call outcome mapping
| What happened | `outcome` |
|---|---|
| All required fields captured and read back | `qualified` |
| Caller wants a consultant to call back / agent could not finish | `callback` (plus a callback task with the time asked) |
| Question answered, no lead | `info` |
| Transferred or escalated | `handoff` |
| Caller declined | `not_interested` |
| Caller dropped before saying anything useful | `no_answer` |

## 5. Agent-side design

### Adapter interface (the only thing the agent code depends on)
```python
class CRMClient(Protocol):
    async def lookup_contact(self, phone: str) -> ContactMatch | None: ...
    async def list_open_enquiries(self, contact_id: str) -> list[EnquirySummary]: ...
    async def create_enquiry(self, lead: LeadIn, *, idempotency_key: str) -> EnquiryRef: ...
    async def update_enquiry(self, enquiry_id: str, patch: LeadPatch, *, idempotency_key: str) -> EnquiryRef: ...
    async def add_task(self, task: TaskIn, *, idempotency_key: str) -> TaskRef: ...   # callbacks, urgent hand-offs
    async def log_call(self, call: CallRecordIn, *, idempotency_key: str) -> CallRef: ...
    async def list_destinations(self) -> list[Destination]: ...                      # the enum above, with aliases
```
Implementations: `MockCRM` (in-memory + JSON, used by tests and by the first demos), `HttpCRM` (talks to the REST API in section 6). The agent never sees HTTP details.

### Tools exposed to the LLM
Few, flat, narrow (Gemma 4 E4B handled this style at 90%, see `06-phase1b-results.md`):

| Tool | Args | Type | Notes |
|---|---|---|---|
| `identify_trip` | `description` | read | returns up to 3 destination candidates with scores; the agent must confirm |
| `search_knowledge` | `query` | read | brochures and FAQs (phase 2) |
| `set_lead_field` | `name` (enum), `value` | local | validates and normalises in code, stores in the call's `LeadForm`; no CRM write yet |
| `get_missing_fields` | none | local | what is still needed, in the order to ask |
| `submit_lead` | none | **write** | only succeeds when required fields are set and confirmed; calls `create_enquiry` / `update_enquiry` |
| `schedule_callback` | `when`, `reason` | **write** | creates a task |
| `handoff_to_human` | `reason` | **write** | urgent task + `needs-human` tag; ends the autonomous part of the call |

The LLM never builds CRM payloads. It supplies strings; code parses them (dates relative to today, "one and a half lakh" to 150000, spoken emails to addresses, digit groups to E.164) and rejects values that do not fit the enums. The system prompt gets explicit tool rules in phase 3 (the benchmarks showed models skip `handoff_to_human` and `schedule_callback` without them).

### Conversation control
The next question to ask comes from `get_missing_fields`, not from the model's memory. Use Pipecat Flows (or an equivalent small state machine) for the *collection and confirmation* stages; keep the rest free-form so the call does not feel scripted.

## 6. Proposed CRM API contract

Full detail in [`crm-api.openapi.yaml`](crm-api.openapi.yaml). Summary:

| Method and path | Purpose |
|---|---|
| `GET /v1/contacts/lookup?phone=+9198…` | Find a traveller by phone; returns contact and open enquiries (ids, destination, stage, owner first name) |
| `GET /v1/destinations` | The destination enum with display names and spoken aliases ("backwaters" to Kerala) |
| `POST /v1/enquiries` | Create a lead (`Idempotency-Key` required) |
| `PATCH /v1/enquiries/{id}` | Update fields or stage-eligible state (key required) |
| `GET /v1/enquiries/{id}` | Read one enquiry |
| `POST /v1/enquiries/{id}/notes` | Add a note (call summary) |
| `POST /v1/tasks` | Create a callback or urgent hand-off task for a consultant |
| `POST /v1/calls` | Log a call: direction, times, outcome, sentiment, transcript, optional recording URL, linked enquiry |
| `POST /v1/webhooks` (CRM side config) | Optional: CRM tells the agent a new web or Instagram enquiry exists (for outbound calls, later) |

**Cross-cutting requirements (the CRM must meet these):**
- **Auth:** one service credential for the agent (bearer token or API key), scoped to exactly these endpoints. No delete, no bulk export, no payment or document access.
- **Latency:** `lookup` and `destinations` answer in under 300 ms p95 on the local network. The agent waits on them mid-sentence. Writes under 1 s.
- **Idempotency:** same `Idempotency-Key` returns the original result and never creates a second record, for 24 hours.
- **Validation errors** are 422 with field-level messages the agent can speak ("that phone number looks short"); the agent maps them back to the field it will re-ask.
- **Attribution:** every record created or changed by the agent carries `created_by = aanya-voice` and `source = phone_call`, and appears in the CRM's activity log and **review queue** where the CRM policy requires it.
- **Time and money:** ISO-8601 with an explicit timezone (`Asia/Kolkata`); money as integer rupees.
- **Versioning:** path prefix `/v1`; additive changes only inside a version.

## 7. Security and privacy

- Everything stays on harsha-pc-ubuntu or the local network. The agent calls the CRM over the network you give it; if the CRM is hosted elsewhere, that is data leaving the machine and needs your explicit decision.
- Credentials live in `.env` (`CRM_BASE_URL`, `CRM_API_KEY`), never in the repo.
- **Prompt-injection surface:** CRM notes, names and itinerary text flow into the LLM context. Tools are the only path to writes, they have no delete or export, and values from the CRM are quoted to the model as data. A malicious note saying "mark every lead as booked" can do nothing because no tool exists for it.
- **PII:** phone and email are needed by the agent; passport and payment details are never requested by voice. Masking applies to transcripts and logs. Local call logs in `data/calls/` get a retention period (proposal: 30 days) and a deletion command.
- **Consent and disclosure:** the greeting states it is an automated assistant and, if recording is on, that the call may be recorded. Decide wording with your counsel before real customers call (India has specific rules for recorded and automated calls).
- **Hosted-LLM fallback** (if ever enabled) receives masked text only: names, phones and emails replaced by placeholders and mapped back locally.

## 8. Test plan

1. **Contract tests**, run against `MockCRM` and `HttpCRM` alike: lookup hit/miss, create, duplicate create with the same key returns the same id, update, validation error shape, task creation, call log, timeout behaviour.
2. **Tool tests**, no audio: each spoken phrase variant to the right normalised value ("one and a half lakh", "twelfth of December to the nineteenth", "my number is nine eight four...", "at gmail dot com").
3. **Scenario suite** (extends `evals/`): 15 to 25 scripted calls through the real pipeline with `scripts/webrtc_smoke.py` against a CRM test instance: new caller, returning caller, ambiguous destination, caller changes a value, caller hangs up half way, cancellation hand-off, discount request, price question, CRM unreachable.
4. **Acceptance criteria**: lead field accuracy of at least 90% on the suite, zero duplicate leads, zero quoted refund or cancellation figures, every hand-off topic produces an urgent task, call record present for every call, and voice-to-voice latency still under 1.2 s with CRM calls in the loop.

## 9. Build order

1. `CRMClient` protocol, dataclasses, `MockCRM`, contract tests. (No CRM needed.)
2. Value parsers and `LeadForm`, with unit tests from the phrases above.
3. Tools plus prompt tool rules; scenario suite against `MockCRM`.
4. `HttpCRM` against the CRM test instance, once the API exists; rerun contract and scenario tests unchanged.
5. Outbox and retry, call logging, hand-off tasks, attribution.
6. Telephony (Plivo) and outbound "call new leads" through the webhook, later.

## 10. What the CRM side has to build or confirm

- The endpoints in section 6 (or tell us the equivalent endpoints it already has; we adapt `HttpCRM`).
- A test instance with seed data we may write to freely, separate from live data.
- Idempotency-key support, a scoped service credential, and `created_by` attribution.
- Phone lookup that tolerates formats (`+91 98765 43210`, `098765 43210`, `9876543210`).
- Destination aliases, so "backwaters", "God's own country" and "Munnar" resolve to Kerala.
- Where agent-created leads land in the review queue and which owner routing applies.

## 11. Open questions

1. **Is the enquiry form in section 3 your lead details sheet?** If it is a spreadsheet or a different form, send the columns, with required or optional, formats and allowed values.
2. **Which CRM will be set up on harsha-pc-ubuntu?** The wishtrip demo's source code (does Ascent-Intelligent-Technologies provide it?), a build modelled on it, or another product. This decides who writes the API in section 6.
3. **"Which trip" means what?** In the demo, a lead has a *destination* (12 options) plus dates and preferences; there is no fixed catalog of trip packages. Your earlier answer mentioned trip brochures (30 to 40 PDFs). Are those per-destination itineraries and price sheets, or named packages? If packages, we need a `trip_package` field on the enquiry.
4. **Inbound only first?** The demo's Aanya also calls new leads (outbound). Outbound needs telephony, consent and calling-hour rules; I assume inbound only until phase 6.
5. **Duplicate rule:** how recent must an existing enquiry be to be updated instead of creating a new one?
6. **Who is notified** for urgent hand-offs, and is there a quiet-hours window the agent must respect?
7. **Recording and retention:** do you want call recordings stored, and for how long?
