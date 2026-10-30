#!/usr/bin/env bash
# Train the 7K LoRA (ADR-0048) on a Linux GPU machine that isn't meow, e.g. a UCL CS GPU workstation (RTX 4090).
#
#   tar -xf ~/lora-job.tar -C /scratch0/$USER/        # the job tools/pack_lora_job.py made: code/, dataset/, this file
#   bash /scratch0/$USER/lora-job/lora_remote.sh [steps=1000] [batch=8] [grad_acc=4]
#   MIN_LN=0.5 RUN=ln1 EVERY=500 bash .../lora_remote.sh 3000 4 8   # long-note charts only, 16 GB card
#   MIN_LN=0.1 RUN=mix1 RANK=128 EVERY=500 bash .../lora_remote.sh 3000   # long-note and mixed charts
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
RUN=${RUN:-run}
EVERY=${EVERY:-250}
RANK=${RANK:-64}
SAVE=${SAVE:-$HOME/proj7k-lora-out}
[ "$RUN" = run ] || SAVE="$SAVE/$RUN"
mkdir -p "$WORK" "$SAVE"
export PROJ7K_MAPPERATORINATOR="$WORK/mapperatorinator" HF_HOME="$WORK/hf" UV_CACHE_DIR="$WORK/uv-cache"
# wandb stages a copy of every checkpoint under ~/.local/share/wandb even with logging off; a 10 GB home fills up
export WANDB_DIR="$WORK/wandb" WANDB_DATA_DIR="$WORK/wandb" WANDB_ARTIFACT_DIR="$WORK/wandb/artifacts" \
  WANDB_CACHE_DIR="$WORK/wandb/cache"
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
nvidia-smi

PY="$WORK/p7k-venv/bin/python"
[ -x "$PY" ] || uv venv -q -p 3.14 "$WORK/p7k-venv"
uv pip install -q -p "$PY" -r "$JOB/code/requirements.txt"
export PYTHONPATH="$JOB/code/src"
cd "$JOB/code"
"$PY" -m proj7k.generator.mapperatorinator --install

DATASET="$JOB/dataset"
if [ -n "${MIN_LN:-}" ]; then
  DATASET="$WORK/dataset-ln"
  "$PY" tools/train_lora.py subset --dataset "$JOB/dataset" --out "$DATASET" --min-ln-share "$MIN_LN" \
    --min-stars "${MIN_STARS:-0}"
fi

save() {
  for d in "$WORK/$RUN"/run/checkpoints/checkpoint_*/lora; do
    [ -d "$d" ] || continue
    n=$(basename "$(dirname "$d")")
    mkdir -p "$SAVE/$n" && cp -ru "$d" "$SAVE/$n/"
  done
  cp -u "$WORK/$RUN"/train.log "$WORK/$RUN"/command.json "$SAVE/" 2>/dev/null || true
}
( while sleep 300; do save; done ) &
SAVER=$!
trap 'save; kill $SAVER 2>/dev/null || true' EXIT

"$PY" tools/train_lora.py train --dataset "$DATASET" --out "$WORK/$RUN" \
  --steps "$STEPS" --batch "$BATCH" --grad-acc "$ACC" --workers 4 --checkpoint-every "$EVERY" --rank "$RANK"
save
echo "LoRA checkpoints in $SAVE"
