#!/usr/bin/env bash
# Run every phase 1b benchmark one after another (they share the GPU, so never in parallel).
# Usage: evals/bench/run_all.sh [gemma4:latest qwen3.5:9b]
set -euo pipefail
cd "$(dirname "$0")/../.."
LLMS=("${@:-gemma4:latest qwen3.5:9b}")
mkdir -p evals/results
uv run python evals/bench/gen_audio.py > evals/results/gen_audio.log 2>&1
uv run python evals/bench/bench_stt.py \
  large-v3-turbo:cuda:int8_float16 \
  large-v3-turbo:cuda:int8_float16:prompt \
  large-v3:cuda:int8_float16 \
  large-v3-turbo:cpu:int8 > evals/results/stt.log 2>&1
uv run python evals/bench/bench_llm.py ${LLMS[@]} --repeats 3 > evals/results/llm.log 2>&1
uv run python evals/bench/bench_tts.py > evals/results/tts.log 2>&1
for m in ${LLMS[@]}; do
  uv run python evals/bench/bench_e2e.py --llm "$m" > "evals/results/e2e_${m//[:\/]/_}.log" 2>&1
done
echo done
