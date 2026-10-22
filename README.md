# local-voice-agent

A fully local voice agent for customer support and sales: speak to it, it answers from a knowledge base you supply, and it reads and writes your CRM.

**Status:** phase 1 done (live voice loop, no tools, knowledge or CRM yet). Start with the docs. **Scope: English only first**; Hindi and Telugu are deferred to phase 8.

| Doc | Contents |
|---|---|
| [docs/00-requirements.md](docs/00-requirements.md) | Product requirements, decisions, open questions (start here) |
| [docs/05-indic-voice-stack.md](docs/05-indic-voice-stack.md) | Hindi / Telugu / Indian-English speech models, GPU placement, telephony notes |
| [docs/06-phase1b-results.md](docs/06-phase1b-results.md) | Measured English model bake-off and the chosen STT, LLM and TTS |
| [docs/07-crm-integration.md](docs/07-crm-integration.md) | How the agent integrates with the CRM: lead fields, call flow, adapter, proposed API, tests (with [crm-api.openapi.yaml](docs/crm-api.openapi.yaml)) |
| [docs/01-infra-requirements.md](docs/01-infra-requirements.md) | Hardware inventory, GPU memory budget, latency targets, prerequisites |
| [docs/02-stack-research.md](docs/02-stack-research.md) | Framework, STT, LLM, TTS, RAG and CRM options compared, with sources |
| [docs/03-architecture.md](docs/03-architecture.md) | Pipeline, components, turn lifecycle, tools, safety, repo layout |
| [docs/04-implementation-plan.md](docs/04-implementation-plan.md) | Phased plan, exit criteria, risks, open questions |

## Dev setup (phase 0)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # once
sudo apt install espeak-ng ffmpeg                   # system audio deps
cp .env.example .env
uv sync
uv run python scripts/check_env.py                  # all checks should pass
```

## Run the voice agent

```bash
ollama create gemma4-voice -f ollama/Modelfile.voice   # once: pins context size
uv run python -m agent.bot                             # loads models (about 5 s), then serves
# open http://localhost:7860 in a browser, allow the microphone, press Connect
```

Automated test call (plays recordings as the caller and times the replies; bot must be running):

```bash
uv run python evals/bench/gen_audio.py                 # once: makes the test audio
uv run python scripts/webrtc_smoke.py evals/data/audio/clean/u24.wav evals/data/audio/clean/u03.wav
```

Per-turn latency breakdowns print in the bot log; each call's transcript is saved in `data/calls/`.

## Chosen stack (phase 1b); full pipeline still to be built

Pipecat · Silero VAD + Smart Turn v3 · faster-whisper large-v3-turbo · Gemma 4 E4B via Ollama (Qwen3.5-9B ruled out for now) · Kokoro TTS on GPU (svara-TTS if an Indian-English voice matters) · LanceDB + bge-m3 · self-hosted CRM behind a `CRMClient` adapter · Plivo later. Phase 1b (English bake-off) is done; see docs/06. Hindi/Telugu (IndicConformer, svara-TTS) are phase 8.
