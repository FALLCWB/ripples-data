#!/bin/bash
# Run full Redis cross-domain sweep: 3 actions x 3 reps
set -euo pipefail
cd "$(dirname "$0")/.."
LOGDIR="$(cd "$(dirname "$0")/.." && pwd)/data/crossdomain"
mkdir -p "$LOGDIR"
export WARMUP_S=180 OBSERVATION_S=180

for action in redis_set_1 redis_mset_100 redis_flushdb; do
    echo "===== ACTION: $action ====="
    ./exp_crossdomain/run_redis.sh "$action" 3 2>&1 | tee -a "$LOGDIR/sweep_all_redis.log"
done
echo "=== ALL REDIS DONE ==="
