#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
LOGDIR=$(cd "$(dirname "$0")/.." /home/lemos/research/ripples-paper/data/crossdomain/home/lemos/research/ripples-paper/data/crossdomain pwd)/data/crossdomain
mkdir -p "$LOGDIR"
export WARMUP_S=180 OBSERVATION_S=180

for action in docker_run_10 docker_run_50; do
    echo "===== ACTION: $action ====="
    ./exp_crossdomain/run_dockerd.sh "$action" 3 2>&1 | tee -a "$LOGDIR/sweep_remaining.log"
done
echo "=== DONE ==="
