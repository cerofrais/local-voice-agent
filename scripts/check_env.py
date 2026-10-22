"""Phase 0 environment check. Run: uv run python scripts/check_env.py"""

import json
import shutil
import subprocess
import sys
import urllib.request

from agent.config import get_settings

OK, WARN, FAIL = "ok  ", "warn", "FAIL"
results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    results.append((status, name, detail))


def run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr).strip()


def check_python() -> None:
    v = sys.version_info
    record(OK if v[:2] == (3, 12) else FAIL, "python", f"{v.major}.{v.minor}.{v.micro}")


def check_gpu() -> None:
    if not shutil.which("nvidia-smi"):
        record(FAIL, "gpu", "nvidia-smi not found")
        return
    code, out = run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader"])
    if code != 0:
        record(FAIL, "gpu", out.splitlines()[0] if out else "nvidia-smi failed (reboot after driver update?)")
    else:
        record(OK, "gpu", out)


def check_binaries() -> None:
    for b in ("espeak-ng", "ffmpeg", "docker", "ollama"):
        record(OK if shutil.which(b) else FAIL, f"binary:{b}", shutil.which(b) or "missing")


def check_ollama() -> None:
    s = get_settings()
    base = s.llm_base_url.removesuffix("/v1")
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=5) as r:
            models = {m["name"] for m in json.load(r)["models"]}
    except Exception as e:
        record(FAIL, "ollama", f"not reachable at {base}: {e}")
        return
    wanted = s.llm_model if ":" in s.llm_model else f"{s.llm_model}:latest"
    record(OK if wanted in models else WARN, f"ollama model {s.llm_model}", "present" if wanted in models else f"missing; have {sorted(models)}")


def check_imports() -> None:
    for mod in ("pipecat", "faster_whisper", "kokoro_onnx", "onnxruntime", "ctranslate2"):
        try:
            __import__(mod)
            record(OK, f"import:{mod}")
        except Exception as e:
            record(FAIL, f"import:{mod}", str(e))
    try:
        import onnxruntime

        providers = onnxruntime.get_available_providers()
        record(OK if "CUDAExecutionProvider" in providers else WARN, "onnxruntime providers", ", ".join(providers))
    except Exception:
        pass


def main() -> int:
    for fn in (check_python, check_gpu, check_binaries, check_ollama, check_imports):
        fn()
    for status, name, detail in results:
        print(f"[{status}] {name:<28} {detail}")
    failed = [r for r in results if r[0] == FAIL]
    print(f"\n{len(failed)} failing check(s)" if failed else "\nall required checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
