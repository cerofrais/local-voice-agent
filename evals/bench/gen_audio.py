"""Synthesise the test utterances with Kokoro and make noisy and phone-band variants.

Caveat: synthetic speech is cleaner and more regular than real callers, and Kokoro has no Indian
voices, so STT numbers from this set are optimistic. Put real recordings in evals/real/ as
<name>.wav (16 kHz mono) plus <name>.txt (reference transcript) to include them.

Run: uv run python evals/bench/gen_audio.py
"""

import numpy as np
import soxr
from common import (
    AUDIO_DIR,
    KOKORO_MODEL,
    KOKORO_VOICES,
    SAMPLE_RATE,
    load_utterances,
    write_wav,
)
from kokoro_onnx import Kokoro

VOICES = ["af_heart", "am_michael", "bf_emma", "bm_george", "af_bella", "am_adam"]
SPEEDS = [1.0, 0.92, 1.08]


def noisy(x: np.ndarray, rng: np.random.Generator, snr_db: float = 6.0) -> np.ndarray:
    # Pink-ish noise (cumulative filtering of white noise) at the requested SNR.
    n = rng.standard_normal(len(x)).astype(np.float32)
    n = np.convolve(n, np.ones(8, dtype=np.float32) / 8, mode="same") + 0.3 * rng.standard_normal(len(x))
    sig_p = float(np.mean(x**2)) + 1e-9
    n *= np.sqrt(sig_p / (10 ** (snr_db / 10)) / (float(np.mean(n**2)) + 1e-9))
    return (x + n).astype(np.float32)


def phone_band(x: np.ndarray) -> np.ndarray:
    # 8 kHz, 300-3400 Hz band, mu-law 8-bit companding, then back to 16 kHz (what a Plivo call looks like).
    x8 = soxr.resample(x, SAMPLE_RATE, 8000)
    spec = np.fft.rfft(x8)
    freqs = np.fft.rfftfreq(len(x8), 1 / 8000)
    spec[(freqs < 300) | (freqs > 3400)] = 0
    x8 = np.fft.irfft(spec, n=len(x8)).astype(np.float32)
    mu = 255.0
    x8 = np.clip(x8, -1, 1)
    comp = np.sign(x8) * np.log1p(mu * np.abs(x8)) / np.log1p(mu)
    comp = np.round((comp + 1) / 2 * 255) / 255 * 2 - 1
    x8 = np.sign(comp) * ((1 + mu) ** np.abs(comp) - 1) / mu
    return soxr.resample(x8.astype(np.float32), 8000, SAMPLE_RATE)


def main() -> None:
    kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
    rng = np.random.default_rng(7)
    for i, utt in enumerate(load_utterances()):
        voice, speed = VOICES[i % len(VOICES)], SPEEDS[i % len(SPEEDS)]
        voice_lang = "en-gb" if voice.startswith("b") else "en-us"
        samples, sr = kokoro.create(utt["text"], voice=voice, speed=speed, lang=voice_lang)
        x = soxr.resample(samples.astype(np.float32), sr, SAMPLE_RATE)
        pad = np.zeros(int(0.3 * SAMPLE_RATE), dtype=np.float32)
        x = np.concatenate([pad, x, pad])
        write_wav(AUDIO_DIR / "clean" / f"{utt['id']}.wav", x)
        write_wav(AUDIO_DIR / "noisy" / f"{utt['id']}.wav", noisy(x, rng))
        write_wav(AUDIO_DIR / "phone" / f"{utt['id']}.wav", phone_band(x))
        print(f"{utt['id']} {voice:11s} x{speed:.2f}  {len(x) / SAMPLE_RATE:5.1f}s")


if __name__ == "__main__":
    main()
