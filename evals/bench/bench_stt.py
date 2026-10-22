"""STT bake-off on the generated audio (and any real recordings in evals/real/).

Run: uv run python evals/bench/bench_stt.py large-v3-turbo:cuda:int8_float16 large-v3:cuda:int8_float16
Each argument is model:device:compute_type. Add ':prompt' to bias with trip names.
"""

import sys

import jiwer
from common import (
    AUDIO_DIR,
    CONDITIONS,
    REAL_DIR,
    Timer,
    entity_found,
    gpu_mem_mb,
    load_utterances,
    normalize,
    read_wav,
    save_result,
    summarize,
)
from faster_whisper import WhisperModel

PROMPT = (
    "Travel company call. Trips: Kerala Backwaters Escape, Ladakh Bike Expedition, Goa Beach Getaway, "
    "Rajasthan Royal Heritage Tour, Himachal Manali Snow Trek, Andaman Island Retreat, Bali Honeymoon "
    "Special, Hampi Heritage Weekend, Singapore Family Fun Package, Dubai City Weekender."
)


def items_for(condition: str) -> list[dict]:
    items = [
        {"id": u["id"], "ref": u["text"], "entities": u["entities"], "path": AUDIO_DIR / condition / f"{u['id']}.wav"}
        for u in load_utterances()
    ]
    if condition == "clean" and REAL_DIR.exists():
        for wav in sorted(REAL_DIR.glob("*.wav")):
            txt = wav.with_suffix(".txt")
            if txt.exists():
                items.append({"id": f"real_{wav.stem}", "ref": txt.read_text().strip(), "entities": [], "path": wav})
    return items


def run_config(spec: str) -> dict:
    parts = spec.split(":")
    model_name, device, compute = parts[0], parts[1], parts[2]
    use_prompt = "prompt" in parts[3:]
    mem0 = gpu_mem_mb()
    with Timer() as t_load:
        model = WhisperModel(model_name, device=device, compute_type=compute)
    mem_loaded = gpu_mem_mb() - mem0
    # Warm-up so CUDA init and autotuning are not billed to the first utterance.
    warm = read_wav(AUDIO_DIR / "clean" / "u01.wav")
    list(model.transcribe(warm, language="en", beam_size=1)[0])

    out = {"spec": spec, "load_s": round(t_load.elapsed, 2), "vram_loaded_mb": mem_loaded, "conditions": {}}
    for cond in CONDITIONS:
        refs, hyps, lat, rtf, ent_hit, ent_tot = [], [], [], [], 0, 0
        for it in items_for(cond):
            audio = read_wav(it["path"])
            with Timer() as t:
                segs, _ = model.transcribe(
                    audio,
                    language="en",
                    beam_size=1,
                    condition_on_previous_text=False,
                    initial_prompt=PROMPT if use_prompt else None,
                    vad_filter=False,
                )
                hyp = " ".join(s.text.strip() for s in segs)
            refs.append(normalize(it["ref"]))
            hyps.append(normalize(hyp))
            lat.append(t.elapsed)
            rtf.append(t.elapsed / (len(audio) / 16000))
            for e in it["entities"]:
                ent_tot += 1
                ent_hit += entity_found(e, hyp)
        out["conditions"][cond] = {
            "wer": round(jiwer.wer(refs, hyps), 4),
            "entity_acc": round(ent_hit / max(ent_tot, 1), 4),
            "latency_s": summarize(lat),
            "rtf_mean": round(sum(rtf) / len(rtf), 3),
        }
    out["vram_peak_mb"] = gpu_mem_mb() - mem0
    return out


def main() -> None:
    results = []
    for spec in sys.argv[1:]:
        print(f"== {spec}", flush=True)
        r = run_config(spec)
        results.append(r)
        for cond, c in r["conditions"].items():
            print(f"  {cond:6s} WER {c['wer']:.3f}  entities {c['entity_acc']:.2f}  p50 {c['latency_s']['p50']:.2f}s  p95 {c['latency_s']['p95']:.2f}s  RTF {c['rtf_mean']:.2f}")
        print(f"  load {r['load_s']}s  VRAM loaded {r['vram_loaded_mb']} MB, peak {r['vram_peak_mb']} MB", flush=True)
        del r
    save_result("stt", {"runs": results})


if __name__ == "__main__":
    main()
