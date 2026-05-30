#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
LOGDIR="$(cd "$(dirname "$0")/.." && pwd)/data/crossdomain"
mkdir -p "$LOGDIR"
export WARMUP_S=180 OBSERVATION_S=180

for action in nginx_curl_1 nginx_burst_100 nginx_reload; do
    echo "===== ACTION: $action ====="
    ./exp_crossdomain/run_nginx.sh "$action" 3 2>&1 | tee -a "$LOGDIR/sweep_all_nginx.log"
done
echo "=== ALL NGINX DONE ==="
