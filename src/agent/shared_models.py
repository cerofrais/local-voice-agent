"""Load Whisper and Kokoro once per process instead of once per call.

Pipecat's Whisper and Kokoro services build their model inside the constructor, and the pipeline
keeps a reference after the call ends, so every call leaked about 0.5-1.5 GB of VRAM (measured:
7.9 -> 9.5 -> 10.0 GB over three calls). Patching the model constructors to return a cached
instance fixes the leak and removes the per-call load time.

Single-call use only: one cached model is shared by every pipeline in the process.
"""

from functools import cache

import numpy as np
import pipecat.services.kokoro.tts as kokoro_tts
import pipecat.services.whisper.stt as whisper_stt
from loguru import logger

from agent import cuda
from agent.config import get_settings


@cache
def _whisper(name: str, device: str, compute_type: str):
    from faster_whisper import WhisperModel

    cuda.setup()
    logger.info(f"loading Whisper {name} ({device}, {compute_type}) once")
    return WhisperModel(name, device=device, compute_type=compute_type)


@cache
def _kokoro(model_path: str, voices_path: str):
    from kokoro_onnx import Kokoro

    cuda.setup()
    logger.info("loading Kokoro once")
    return Kokoro(model_path, voices_path)


def install() -> None:
    whisper_stt.WhisperModel = lambda name, device="auto", compute_type="default": _whisper(
        name, device, compute_type
    )
    kokoro_tts.Kokoro = lambda model_path, voices_path: _kokoro(model_path, voices_path)


def warm_up() -> None:
    """Run each model once so CUDA kernels are initialised before the first caller arrives."""
    s = get_settings()
    stt = _whisper(s.stt_model, s.stt_device, s.stt_compute_type)
    list(stt.transcribe(np.zeros(16000, dtype=np.float32), language="en", beam_size=1)[0])
    tts = _kokoro(
        str(s.models_dir / "kokoro" / "kokoro-v1.0.onnx"),
        str(s.models_dir / "kokoro" / "voices-v1.0.bin"),
    )
    tts.create("Warming up.", voice=s.tts_voice, speed=1.0, lang="en-us")
    logger.info("speech models warm")
