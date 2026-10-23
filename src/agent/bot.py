"""Live voice pipeline: browser WebRTC -> VAD + Smart Turn -> Whisper -> Gemma -> Kokoro.

Run: uv run python -m agent.bot            (then open http://localhost:7860)
"""

import asyncio
import threading
import time
from datetime import datetime

import httpx
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import EndTaskFrame, LLMRunFrame, TTSSpeakFrame
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
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

from agent import (
    cuda,  # noqa: F401  (must run before faster_whisper / kokoro_onnx are used)
    shared_models,
)
from agent.call.guard import EscalationGuard
from agent.call.session import CallSession
from agent.call.startup import caller_phone_from, plivo_caller_number, start_session
from agent.config import get_settings
from agent.crm import ResilientCRM, make_crm
from agent.observability.calllog import prune_old_logs, write_call_log
from agent.prompts.base import system_prompt
from agent.tools import register_tools, tools_schema
from agent.tools.lead_tools import LeadTools

settings = get_settings()
crm = make_crm(
    settings.crm_backend,
    base_url=settings.crm_base_url,
    api_key=settings.crm_api_key,
    data_dir=settings.data_dir,
)
_outbox_worker: asyncio.Task | None = None

CALL_LOG_DIR = settings.data_dir / "calls"
KOKORO_MODEL = settings.models_dir / "kokoro" / "kokoro-v1.0.onnx"
KOKORO_VOICES = settings.models_dir / "kokoro" / "voices-v1.0.bin"

transport_params = {
    "webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    # Phone calls: run with `-t plivo --proxy <public host>`; the serializer is attached by create_transport.
    "plivo": lambda: FastAPIWebsocketParams(audio_in_enabled=True, audio_out_enabled=True),
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
prune_old_logs(CALL_LOG_DIR)


def build_pipeline(transport, session: CallSession):
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

    tools = LeadTools(session)
    register_tools(llm, tools)
    context = LLMContext(
        [{"role": "system", "content": system_prompt(session.config, session)}],
        tools=tools_schema(),
    )
    session.messages_provider = lambda: context.messages
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
            EscalationGuard(session),
            llm,
            tts,
            transport.output(),
            assistant_agg,
        ]
    )
    return pipeline, context


async def bot(runner_args: RunnerArguments):
    global _outbox_worker
    if isinstance(crm, ResilientCRM):
        await crm.flush()  # replay anything a previous outage left behind
        if _outbox_worker is None or _outbox_worker.done():
            _outbox_worker = asyncio.create_task(crm.run_forever())

    # The transport goes first: for phone calls it reads the provider handshake that carries the call id.
    transport = await create_transport(runner_args, transport_params)
    call_data = getattr(runner_args, "call_data", None)
    looked_up = ""
    if call_data is not None and call_data.get("call_id") and settings.plivo_auth_id:
        looked_up = await plivo_caller_number(call_data["call_id"], settings.plivo_auth_id, settings.plivo_auth_token)
    session = await start_session(
        crm,
        caller_phone_from(runner_args, settings.dev_caller_phone if call_data is None else "", looked_up),
        duplicate_window_days=settings.duplicate_window_days,
    )
    pipeline, context = build_pipeline(transport, session)

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
    timer: asyncio.Task | None = None

    async def time_limit() -> None:
        await asyncio.sleep(session.config.max_call_minutes * 60)
        logger.info("call time limit reached; asking the agent to wrap up")
        context.add_message(
            {
                "role": "developer",
                "content": "The call has reached its time limit. Wrap up politely in one sentence. "
                "If the details are not saved yet, offer a consultant callback.",
            }
        )
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_connected")
    async def on_connected(_transport, _client):
        nonlocal timer
        logger.info("caller connected")
        if not session.config.enabled:
            logger.warning("the CRM has the voice agent switched off (Settings -> Agents); declining the call")
            line = (
                f"Thanks for calling {session.config.company_name or 'us'}. Our assistant is not taking calls "
                "right now. A consultant will get back to you. Goodbye."
            )
            await task.queue_frames([TTSSpeakFrame(line), EndTaskFrame()])
            return
        # The opening line lives in the system prompt: a lingering "developer" message made the model
        # chat instead of calling tools for the rest of the call (evals/tools/tool_calling.py).
        await task.queue_frames([LLMRunFrame()])
        timer = asyncio.create_task(time_limit())

    @transport.event_handler("on_client_disconnected")
    async def on_disconnected(_transport, _client):
        logger.info("caller disconnected")
        if timer:
            timer.cancel()
        result: dict = {}
        try:
            duration = int((datetime.now(session.started_at.tzinfo) - session.started_at).total_seconds())
            result = await asyncio.wait_for(session.finalize(context.messages, duration_s=duration), timeout=25)
        except Exception as e:  # the call log below must still be written
            logger.exception(f"end-of-call CRM wrap-up failed: {e}")
        path = write_call_log(
            CALL_LOG_DIR,
            session.call_id,
            {
                "call_id": session.call_id,
                "caller_phone": session.caller_phone,
                "outcome": result.get("outcome"),
                "summary": result.get("summary"),
                "lead": session.form.values,
                "crm_events": session.events,
                "messages": context.messages,
                "turns": turns,
            },
        )
        logger.info(f"call log written to {path}")
        await task.cancel()

    await PipelineRunner(handle_sigint=runner_args.handle_sigint).run(task)


def main() -> None:
    from pipecat.runner.run import main as runner_main

    runner_main()


if __name__ == "__main__":
    main()
