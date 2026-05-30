#!/bin/bash
# Run nginx cross-domain echo experiment.
# Usage: ./run_nginx.sh ACTION REPS
#   ACTION ∈ {nginx_curl_1, nginx_burst_100, nginx_reload}
set -euo pipefail
ACTION="${1:-nginx_curl_1}"
REPS="${2:-3}"
WARMUP_S="${WARMUP_S:-180}"
OBSERVATION_S="${OBSERVATION_S:-180}"
DATA_DIR="$(cd "$(dirname "$0")/.." && pwd)/data/crossdomain"
mkdir -p "$DATA_DIR"
cd "$(dirname "$0")/.."

# Build image once
cat > /tmp/Dockerfile.echo-nginx << 'EOF'
FROM nginx:alpine
RUN apk add --no-cache python3 py3-pip py3-psutil apache2-utils curl bash
COPY memdump_runner.py /opt/
COPY nginx_entrypoint.sh /opt/
RUN chmod +x /opt/nginx_entrypoint.sh
ENTRYPOINT ["/opt/nginx_entrypoint.sh"]
EOF

cat > exp_crossdomain/nginx_entrypoint.sh << 'EOF'
#!/bin/sh
# Start nginx in background as daemon
nginx
sleep 1
exec python3 /opt/memdump_runner.py
EOF
chmod +x exp_crossdomain/nginx_entrypoint.sh

docker build -q -t echo-nginx-img -f /tmp/Dockerfile.echo-nginx exp_crossdomain/ > /dev/null

for rep in $(seq 1 $REPS); do
    EXP_NAME="nginx_${ACTION}_rep${rep}_$(date +%s)"
    echo "=== Starting $EXP_NAME ==="
    docker rm -f "echo-nginx-${rep}" 2>/dev/null || true
    docker run --rm \
        --name "echo-nginx-${rep}" \
        -v "$DATA_DIR":/data \
        -e EXP_NAME="$EXP_NAME" \
        -e TARGET_PROCESS=nginx \
        -e ACTION_KIND="$ACTION" \
        -e WARMUP_S="$WARMUP_S" \
        -e OBSERVATION_S="$OBSERVATION_S" \
        echo-nginx-img 2>&1 | tee "$DATA_DIR/${EXP_NAME}.log"
    echo "=== $EXP_NAME done ==="
done
