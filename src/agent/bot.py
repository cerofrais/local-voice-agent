"""Live voice pipeline: browser WebRTC -> VAD + Smart Turn -> Whisper -> Gemma -> Kokoro.

Run: uv run python -m agent.bot            (then open http://localhost:7860)
"""

import json
import threading
import time
from datetime import datetime

import httpx
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.kokoro.tts import KokoroTTSService
from pipecat.services.ollama.llm import OLLamaLLMService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import TransportParams

from agent import (
    cuda,  # noqa: F401  (must run before faster_whisper / kokoro_onnx are used)
    shared_models,
)
from agent.config import get_settings
from agent.prompts.base import GREETING_INSTRUCTION, system_prompt

settings = get_settings()

CALL_LOG_DIR = settings.data_dir / "calls"
KOKORO_MODEL = settings.models_dir / "kokoro" / "kokoro-v1.0.onnx"
KOKORO_VOICES = settings.models_dir / "kokoro" / "voices-v1.0.bin"

transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
}


def _keep_llm_resident() -> None:
    """Ollama unloads a model after 5 idle minutes unless told otherwise; a cold start takes ~25 s.

    Requests on the OpenAI-compatible endpoint cannot set keep_alive, so ping the native API.
    """
    base = settings.llm_base_url.removesuffix("/v1")
    body = {"model": settings.llm_model, "keep_alive": -1, "prompt": "", "stream": False}
    while True:
        try:
            httpx.post(f"{base}/api/generate", json=body, timeout=120)
        except Exception as e:  # Ollama may not be up yet
            logger.warning(f"LLM keep-alive failed: {e}")
        time.sleep(120)


threading.Thread(target=_keep_llm_resident, daemon=True, name="llm-keepalive").start()
shared_models.install()
shared_models.warm_up()


def build_pipeline(transport):
    stt = WhisperSTTService(
        device=settings.stt_device,
        compute_type=settings.stt_compute_type,
        settings=WhisperSTTService.Settings(
            model=settings.stt_model,
            language=Language.EN,
            initial_prompt=settings.stt_prompt or None,
        ),
    )
    llm = OLLamaLLMService(
        base_url=settings.llm_base_url,
        settings=OLLamaLLMService.Settings(
            model=settings.llm_model,
            max_tokens=120,
            # Thinking must be off for voice: the default adds seconds of silence (docs/06).
            extra={"reasoning_effort": "none"},
        ),
    )
    tts = KokoroTTSService(
        model_path=str(KOKORO_MODEL),
        voices_path=str(KOKORO_VOICES),
        settings=KokoroTTSService.Settings(voice=settings.tts_voice),
    )

    context = LLMContext([{"role": "system", "content": system_prompt()}])
    # Smart Turn v3 is Pipecat's default turn-stop strategy; Silero VAD feeds it.
    user_agg, assistant_agg = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
        ),
    )
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_agg,
            llm,
            tts,
            transport.output(),
            assistant_agg,
        ]
    )
    return pipeline, context


async def bot(runner_args: RunnerArguments):
    transport = await create_transport(runner_args, transport_params)
    pipeline, context = build_pipeline(transport)

    latency = UserBotLatencyObserver()
    turns: list[dict] = []

    @latency.event_handler("on_latency_breakdown")
    async def on_breakdown(_observer, breakdown):
        lines = breakdown.turn_contribution_lines()
        logger.info("latency\n  " + "\n  ".join(lines))
        turns.append({"total_s": round(breakdown.total_secs, 3), "parts": lines})

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=False),
        observers=[latency],
    )

    @transport.event_handler("on_client_connected")
    async def on_connected(_transport, _client):
        logger.info("caller connected")
        context.add_message({"role": "developer", "content": GREETING_INSTRUCTION})
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(_transport, _client):
        logger.info("caller disconnected")
        CALL_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = CALL_LOG_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
        path.write_text(
            json.dumps({"messages": context.messages, "turns": turns}, indent=2, default=str)
        )
        logger.info(f"call log written to {path}")
        await task.cancel()

    await PipelineRunner(handle_sigint=runner_args.handle_sigint).run(task)


def main() -> None:
    from pipecat.runner.run import main as runner_main

    runner_main()


if __name__ == "__main__":
    main()
