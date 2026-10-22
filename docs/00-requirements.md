# 00 — Requirements and decisions

_Captured 2026-10-22 from the owner's answers to the open questions. This is the source of truth; the other docs are written to satisfy it._

> **Scope decision (2026-10-22): English only first.** The browser milestone, phases 1–7 of the plan, and the GPU budget in `01` target English (Indian-accented callers). Hindi and Telugu are the longer-term goal and are now **phase 8**; everything about them in this file and in `05-indic-voice-stack.md` is kept as a deferred reference, not a current requirement. The architecture keeps a seam for it (language is a config value, not hard-coded). The tables below describe the eventual target unless marked otherwise.

## 1. The product in one paragraph

A voice agent for a **travel business** that picks up a call, works out **which trip** the caller means, **collects every field required by the lead details sheet**, answers basic questions about trips from a supplied knowledge base, and writes the lead to a **self-hosted CRM**. Callers speak **English, Hindi or Telugu, by their own preference**.

## 2. Answers and what they imply

| # | Topic | Answer | Implication |
|---|---|---|---|
| 1 | Languages | **Now: English only.** Later: English, Hindi, Telugu, chosen by the caller | The biggest change to the plan. Kokoro covers only English and a weak Hindi, and has no Telugu and no Indian-English voice. Speech models become the main risk. See `05-indic-voice-stack.md`. |
| 2 | CRM | Self-hosted | Keep the `CRMClient` adapter. Which product is still unknown (question A below). |
| 3 | Channel | Browser first; Plivo or another telephony provider later | Pipecat has a Plivo serializer (8 kHz mu-law over WebSocket). Phone audio hurts accuracy, especially in Hindi/Telugu, so test it early with recordings. |
| 4 | Use case | Support plus sales qualification. Must: (1) receive a call, (2) identify which trip is being discussed, (3) collect all details for the lead sheet, (4) answer basic questions | Adds two first-class components: a **trip resolver** and a **lead-sheet form**. See `03-architecture.md`. |
| 5 | Knowledge | PDFs, website, FAQ sheet, help center; about 30–40 PDFs of 3–5 pages (roughly 100–200 pages) | Small. A local index is trivial and retrieval is fast. The work is in extraction quality, not scale. Likely trip itineraries, which also become a structured trip catalog. |
| 6 | Voice | Generic good voice; Indian accent helps | Rules out Kokoro for English (no Indian voice). Points to an Indic TTS such as svara-TTS or Indic Parler-TTS. |
| 7 | Data policy | Fully local is a hard requirement; a hosted LLM may be a fallback if local falls short | Default path never leaves the machine. The fallback must be off by default, switchable per environment, and send as little personal data as possible (see section 4). |

## 3. Success criteria for the first milestone (browser)

A tester can, in English (later also Hindi and Telugu):

1. Say which trip they're interested in, in their own words, and have the agent confirm the right one (or ask a clarifying question if ambiguous).
2. Be asked for each required lead field, one at a time, and have the values captured correctly (names, phone number, travel dates, group size, budget, etc.).
3. Ask a basic question ("what's included?", "how many days?", "when does it depart?") and get an answer that matches the PDFs, or an honest "I don't have that".
4. End the call with a read-back of the details and a lead created in the CRM.
5. (Phase 8) Switch language mid-call if they ask to.

Measured targets: voice-to-voice latency under about 1.2 s for English (Hindi/Telugu targets are set in phase 8), lead field accuracy above 90% on the scenario suite, and no invented prices or dates.

## 4. Hosted-LLM fallback: how I'll interpret "fully local is a hard requirement, but a hosted LLM can be a fallback"

- **Default is local.** Nothing is sent out unless the fallback is explicitly enabled by config.
- The fallback applies to the **LLM only**. Speech (audio) never leaves the machine. If you also want hosted STT/TTS as a fallback, say so; I'm assuming not.
- When enabled, send only what the model needs: no phone numbers, emails or full names (replace with placeholders and map them back locally), and no CRM dump.
- Log every fallback call so you can see how often it happens. If it happens a lot, that's the signal to buy a bigger GPU rather than lean on the cloud.
- A hosted LLM does not fix Indic speech quality. Only a local STT/TTS swap or a bigger GPU does.

## 5. Remaining questions

With English first, **A, B, D, E and G matter now**. F is still useful (Indian-accented English recordings). C and H can wait; C matters again at phase 8, H at phase 6.

| # | Question | Why it matters |
|---|---|---|
| A | **Which self-hosted CRM?** _Update: you shared a reference, the "wishtrip CRM" demo at https://demo-travel-crm-eight.vercel.app/login (built by Ascent-Intelligent-Technologies). It is a travel-consultant CRM with enquiries/leads, itineraries, trips, travellers, bookings, payments, documents, visas, vouchers, suppliers, calls and inbox, and it already lists a voice agent ("Aanya") and a chat agent ("Wish"). Its login page says the data is generated in the browser, so I could see no backend or API. We still have to stand up our own instance on harsha-pc-ubuntu. Please tell me: do you have this CRM's source code or an API spec, or should we build a small self-hosted CRM modelled on its entities?_ (Twenty, EspoCRM, Frappe CRM, SuiteCRM, Odoo, something custom, or none yet) | Decides the adapter and how lead fields map. If none yet, I recommend EspoCRM for a simple admin-configurable lead form, or Twenty if you want a modern API-first stack. |
| B | _Update 2026-10-23: the reference CRM's enquiry form looks like the lead sheet; its fields are listed in `07-crm-integration.md` section 3. Please confirm._ **What are the exact columns of the lead details sheet?** Please paste or describe them (required vs optional, formats, allowed values). Is the "sheet" a spreadsheet, or a CRM lead form? | The agent's whole conversation is built from this schema. |
| C | **What language are the PDFs and website in?** English only, or also Hindi/Telugu? | Cross-language retrieval works (bge-m3), but answers about names and prices must be read aloud correctly in the caller's language. |
| D | **Are the PDFs digital text or scans?** Do they contain price tables? | Scans need OCR. Tables need extra care. |
| E | **How do callers refer to trips?** By name, destination, dates, or loosely ("the Kerala one")? | Shapes the trip resolver and whether we need aliases in Hindi/Telugu. |
| F | **Do you have any real call recordings** (even 10–20) in these languages, including code-mixed speech? | The most valuable single input for choosing STT. If none, I'll generate test audio, but real calls beat synthetic. |
| G | **Ok to treat the hosted fallback as LLM-only?** (section 4) | Avoids a privacy surprise. |
| H | **Plivo account/region and whether outbound calls are planned** | Indian outbound calling has regulatory requirements (e.g. DLT registration). Not needed for the browser milestone. |
