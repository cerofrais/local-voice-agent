"""Shared helpers for the phase 1b benchmarks."""

import ctypes
import glob
import json
import re
import site
import statistics
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
from num2words import num2words


def preload_cuda_libs() -> None:
    """CTranslate2 dlopen()s libcublas/libcudnn by name; load the pip-installed copies first."""
    for sp in site.getsitepackages():
        for pattern in (
            "nvidia/cublas/lib/libcublas*.so*",
            "nvidia/cuda_nvrtc/lib/*.so*",
            "nvidia/cudnn/lib/libcudnn*.so*",
        ):
            for lib in sorted(glob.glob(f"{sp}/{pattern}")):
                try:
                    ctypes.CDLL(lib, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def use_gpu_onnx() -> None:
    """Load onnxruntime-gpu's CUDA libraries and point kokoro-onnx at the CUDA provider."""
    import os

    os.environ.setdefault("ONNX_PROVIDER", "CUDAExecutionProvider")
    try:
        import onnxruntime

        onnxruntime.preload_dlls()
    except Exception:
        pass


preload_cuda_libs()
use_gpu_onnx()

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "evals" / "corpus"
AUDIO_DIR = ROOT / "evals" / "data" / "audio"
REAL_DIR = ROOT / "evals" / "real"
RESULTS = ROOT / "evals" / "results"
KOKORO_MODEL = ROOT / "models" / "kokoro" / "kokoro-v1.0.onnx"
KOKORO_VOICES = ROOT / "models" / "kokoro" / "voices-v1.0.bin"

SAMPLE_RATE = 16000
CONDITIONS = ("clean", "noisy", "phone")


def load_utterances() -> list[dict]:
    return json.loads((CORPUS / "utterances.json").read_text())["utterances"]


def load_llm_cases() -> dict:
    return json.loads((CORPUS / "llm_cases.json").read_text())


def gpu_mem_mb() -> int:
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    return int(out.splitlines()[0])


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return s[min(len(s) - 1, round(q * (len(s) - 1)))]


def summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(statistics.fmean(values), 3),
        "p50": round(pct(values, 0.5), 3),
        "p95": round(pct(values, 0.95), 3),
        "max": round(max(values), 3),
    }


def write_wav(path: Path, samples: np.ndarray, rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1, path
        return (
            np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        )


# --- text normalisation for WER and entity checks -------------------------------------------

_DIGIT_WORDS = "zero one two three four five six seven eight nine".split()
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?(?:st|nd|rd|th)?")


def _num_to_words(m: re.Match) -> str:
    tok = m.group(0)
    ordinal = tok[-2:] in ("st", "nd", "rd", "th")
    core = tok[:-2] if ordinal else tok
    core = core.replace(",", "")
    if "." not in core and len(core) >= 4 and not ordinal:
        return " " + " ".join(_DIGIT_WORDS[int(c)] for c in core) + " "
    try:
        n = float(core) if "." in core else int(core)
        return " " + num2words(n, to="ordinal" if ordinal else "cardinal") + " "
    except Exception:
        return " " + core + " "


def normalize(text: str) -> str:
    t = text.lower().replace("&", " and ")
    t = _NUM_RE.sub(_num_to_words, t)
    t = re.sub(r"[-_]", " ", t)
    t = re.sub(r"[^a-z0-9@. ]", " ", t)
    t = re.sub(r"(?<![a-z0-9])\.|\.(?![a-z0-9])", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _digits_only(text: str) -> str:
    words = {w: str(i) for i, w in enumerate(_DIGIT_WORDS)} | {"oh": "0"}
    out = []
    for tok in normalize(text).split():
        if tok in words:
            out.append(words[tok])
    direct = re.sub(r"\D", "", text)
    return "".join(out) if out else direct


def entity_found(entity: dict, hypothesis: str) -> bool:
    kind = entity.get("kind", "text")
    v = entity["v"]
    if kind == "digits":
        return v in _digits_only(hypothesis) or v in re.sub(r"\D", "", hypothesis)
    if kind == "email":
        h = re.sub(r"\b(dot|at)\b", "", normalize(hypothesis).replace("@", " ").replace(".", " "))
        return v in re.sub(r"[^a-z0-9]", "", h)
    return v in normalize(hypothesis)


class Timer:
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.elapsed = time.perf_counter() - self.t0


def save_result(name: str, data: dict) -> Path:
    RESULTS.mkdir(parents=True, exist_ok=True)
    path = RESULTS / f"{name}.json"
    path.write_text(json.dumps(data, indent=2))
    return path
