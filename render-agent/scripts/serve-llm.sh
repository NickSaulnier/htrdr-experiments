#!/usr/bin/env bash
# Serve the agent's LLM with vLLM, as an OpenAI-compatible API on
# http://127.0.0.1:$PORT/v1 (model name "$SERVED_NAME").
#
#   bash render-agent/scripts/serve-llm.sh
#
# Defaults to Qwen/Qwen3.5-9B quantized to FP8 when it loads (about 10 GB
# of VRAM instead of 19 GB in BF16), which leaves an RTX 4090 plenty of room
# for context. The flags follow Qwen's model card: its reasoning and tool
# call parsers, and --language-model-only to skip the vision encoder.
#
# Install vLLM first (no root needed):
#   uv venv ~/venvs/vllm --python 3.12
#   VIRTUAL_ENV=~/venvs/vllm uv pip install vllm==0.31.0 --torch-backend=auto
#
# Extra arguments are passed to `vllm serve`.

set -euo pipefail

VLLM=${VLLM:-$HOME/venvs/vllm/bin/vllm}
MODEL=${MODEL:-Qwen/Qwen3.5-9B}
SERVED_NAME=${SERVED_NAME:-qwen}
PORT=${PORT:-8000}
MAX_MODEL_LEN=${MAX_MODEL_LEN:-32768}
# Fraction of the GPU's memory vLLM may take. The Windows desktop holds a
# few GB of a WSL GPU, so stay well below 1.
GPU_MEM=${GPU_MEM:-0.80}

# FlashInfer compiles its top-k/top-p sampling kernel on first use, which
# needs the CUDA toolkit's nvcc; without it vLLM fails at startup ("Could not
# find nvcc"). vLLM's PyTorch sampler needs nothing extra. Set this to 1 if
# the CUDA toolkit is installed.
export VLLM_USE_FLASHINFER_SAMPLER=${VLLM_USE_FLASHINFER_SAMPLER:-0}

exec "$VLLM" serve "$MODEL" \
  --served-model-name "$SERVED_NAME" \
  --host 127.0.0.1 --port "$PORT" \
  --quantization fp8 \
  --language-model-only \
  --max-model-len "$MAX_MODEL_LEN" \
  --gpu-memory-utilization "$GPU_MEM" \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  "$@"
