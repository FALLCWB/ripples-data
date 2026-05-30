#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
LOGDIR="$(cd "$(dirname "$0")/.." && pwd)/data/crossdomain"
mkdir -p "$LOGDIR"
export WARMUP_S=180 OBSERVATION_S=180

for action in docker_inspect docker_run_1 docker_run_10 docker_run_50; do
    echo "===== ACTION: $action ====="
    ./exp_crossdomain/run_dockerd.sh "$action" 3 2>&1 | tee -a "$LOGDIR/sweep_all_dockerd.log"
done

# Optional 5-min idle baseline run to characterize GC-residual page churn
# with GOGC=off (should be essentially zero; documents the noise floor).
echo "===== IDLE BASELINE (5 min, no action) ====="
WARMUP_S=60 OBSERVATION_S=300 ./exp_crossdomain/run_dockerd.sh noop 1 2>&1 \
    | tee -a "$LOGDIR/sweep_all_dockerd.log"

echo "=== ALL DOCKERD DONE ==="
