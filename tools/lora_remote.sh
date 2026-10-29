#!/usr/bin/env bash
# Train the 7K LoRA (ADR-0048) on a Linux GPU machine that isn't meow, e.g. a UCL CS GPU workstation (RTX 4090).
#
#   tar -xf ~/lora-job.tar -C /scratch0/$USER/        # the job tools/pack_lora_job.py made: code/, dataset/, this file
#   bash /scratch0/$USER/lora-job/lora_remote.sh [steps=1000] [batch=8] [grad_acc=4]
#
# Detached, so it survives the ssh / remote-desktop session (the UCL login shell is tcsh, hence bash -c):
#   tmux new -d -s lora bash -c "bash /scratch0/$USER/lora-job/lora_remote.sh 2>&1 | tee /scratch0/$USER/lora-run.log"
#
# Everything is built in /scratch0/$USER/proj7k-lora (uv, Python 3.14 for proj7k, Mapperatorinator's Python 3.10 and
# PyTorch, the model weights). Scratch is wiped when the session ends, so each checkpoint's LoRA (~43 MB) and the log
# are copied to $SAVE (default ~/proj7k-lora-out) every 5 minutes and once more at the end.
set -euo pipefail
JOB="$(cd "$(dirname "$0")" && pwd)"
STEPS=${1:-1000}
BATCH=${2:-8}
ACC=${3:-4}
WORK=${WORK:-/scratch0/$USER/proj7k-lora}
SAVE=${SAVE:-$HOME/proj7k-lora-out}
mkdir -p "$WORK" "$SAVE"
export PROJ7K_MAPPERATORINATOR="$WORK/mapperatorinator" HF_HOME="$WORK/hf" UV_CACHE_DIR="$WORK/uv-cache"
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
nvidia-smi

PY="$WORK/p7k-venv/bin/python"
[ -x "$PY" ] || uv venv -q -p 3.14 "$WORK/p7k-venv"
uv pip install -q -p "$PY" -r "$JOB/code/requirements.txt"
export PYTHONPATH="$JOB/code/src"
cd "$JOB/code"
"$PY" -m proj7k.generator.mapperatorinator --install

save() {
  for d in "$WORK"/run/run/checkpoints/checkpoint_*/lora; do
    [ -d "$d" ] || continue
    n=$(basename "$(dirname "$d")")
    mkdir -p "$SAVE/$n" && cp -ru "$d" "$SAVE/$n/"
  done
  cp -u "$WORK"/run/train.log "$WORK"/run/command.json "$SAVE/" 2>/dev/null || true
}
( while sleep 300; do save; done ) &
SAVER=$!
trap 'save; kill $SAVER 2>/dev/null || true' EXIT

"$PY" tools/train_lora.py train --dataset "$JOB/dataset" --out "$WORK/run" \
  --steps "$STEPS" --batch "$BATCH" --grad-acc "$ACC" --workers 4
save
echo "LoRA checkpoints in $SAVE"
