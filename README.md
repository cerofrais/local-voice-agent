# local-voice-agent

A fully local voice agent for customer support and sales: speak to it, it answers from a knowledge base you supply, and it reads and writes your CRM.

**Status:** planning. No code yet. Start with the docs. **Scope: English only first**; Hindi and Telugu are deferred to phase 8.

| Doc | Contents |
|---|---|
| [docs/00-requirements.md](docs/00-requirements.md) | Product requirements, decisions, open questions (start here) |
| [docs/05-indic-voice-stack.md](docs/05-indic-voice-stack.md) | Hindi / Telugu / Indian-English speech models, GPU placement, telephony notes |
| [docs/06-phase1b-results.md](docs/06-phase1b-results.md) | Measured English model bake-off and the chosen STT, LLM and TTS |
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

## Chosen stack (phase 1b); full pipeline still to be built

Pipecat · Silero VAD + Smart Turn v3 · faster-whisper large-v3-turbo · Gemma 4 E4B via Ollama (Qwen3.5-9B ruled out for now) · Kokoro TTS on GPU (svara-TTS if an Indian-English voice matters) · LanceDB + bge-m3 · self-hosted CRM behind a `CRMClient` adapter · Plivo later. Phase 1b (English bake-off) is done; see docs/06. Hindi/Telugu (IndicConformer, svara-TTS) are phase 8.
