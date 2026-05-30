#!/bin/bash
# Run Redis cross-domain echo experiment.
# Usage: ./run_redis.sh ACTION REPS
#   ACTION ∈ {redis_set_1, redis_mset_100, redis_flushdb}
#   REPS = number of repetitions (default 3)
set -euo pipefail

ACTION="${1:-redis_set_1}"
REPS="${2:-3}"
WARMUP_S="${WARMUP_S:-180}"   # 3 min warmup (faster than OvS's 5min)
OBSERVATION_S="${OBSERVATION_S:-180}"  # 3 min observation
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$REPO_ROOT/data/crossdomain"
mkdir -p "$DATA_DIR"

cd "$(dirname "$0")/.."

for rep in $(seq 1 $REPS); do
    EXP_NAME="redis_${ACTION}_rep${rep}_$(date +%s)"
    echo "=== Starting $EXP_NAME ==="

    # Cleanup any previous run
    docker rm -f "echo-redis-${rep}" 2>/dev/null || true

    # Run redis + monitor in same container so they share PID namespace.
    # Build a minimal image inline: redis + python + psutil.
    cat > /tmp/Dockerfile.echo-redis << 'EOF'
FROM redis:7-alpine
RUN apk add --no-cache python3 py3-pip py3-psutil
COPY memdump_runner.py /opt/
COPY redis_entrypoint.sh /opt/
RUN chmod +x /opt/redis_entrypoint.sh
ENTRYPOINT ["/opt/redis_entrypoint.sh"]
EOF

    cat > exp_crossdomain/redis_entrypoint.sh << 'EOF'
#!/bin/sh
# Start redis-server in background (no snapshotting)
redis-server --save '' --appendonly no --daemonize yes
sleep 1
# Run memdump runner (foreground, will exit after observation)
exec python3 /opt/memdump_runner.py
EOF
    chmod +x exp_crossdomain/redis_entrypoint.sh

    # Build image (cached after first run)
    docker build -q -t echo-redis-img \
        -f /tmp/Dockerfile.echo-redis \
        exp_crossdomain/ > /dev/null

    # Run experiment
    docker run --rm \
        --name "echo-redis-${rep}" \
        -v "$DATA_DIR":/data \
        -e EXP_NAME="$EXP_NAME" \
        -e TARGET_PROCESS=redis-server \
        -e ACTION_KIND="$ACTION" \
        -e WARMUP_S="$WARMUP_S" \
        -e OBSERVATION_S="$OBSERVATION_S" \
        echo-redis-img 2>&1 | tee "$DATA_DIR/${EXP_NAME}.log"

    echo "=== $EXP_NAME done ==="
done
echo "All reps done."
