#!/bin/bash
# Run dockerd-in-docker cross-domain ripple experiment.
# Actions:
#   docker_inspect — docker inspect on a long-lived container (truly small surface)
#   docker_run_1   — 1 alpine container
#   docker_run_10  — 10 alpine containers
#   docker_run_50  — 50 alpine containers
#
# Each action triggers cgroup setup + image-layer mount + network bridge +
# process exec inside the target dockerd daemon, which leaves ripples.
#
# Go GC control: dockerd is a Go program; the runtime's mark-sweep GC
# can dominate page-level mutation signals if left unconstrained.
# GOGC=off disables automatic GC; we trigger a manual GC via the dockerd
# pprof endpoint (--debug enables it on the dockerd API socket) before
# the action so the post-action ripple is the action's signature, not GC
# noise. See dockerd_entrypoint.sh for the trigger plumbing.
set -euo pipefail
ACTION="${1:-docker_run_1}"
REPS="${2:-3}"
WARMUP_S="${WARMUP_S:-180}"
OBSERVATION_S="${OBSERVATION_S:-180}"
DATA_DIR="$(cd "$(dirname "$0")/.." && pwd)/data/crossdomain"
mkdir -p "$DATA_DIR"
cd "$(dirname "$0")/.."

# Build image: docker:dind + python + psutil + memdump runner + curl for pprof GC trigger
cat > /tmp/Dockerfile.echo-dind << 'EOF'
FROM docker:dind
RUN apk add --no-cache python3 py3-pip py3-psutil curl
COPY memdump_runner.py /opt/
COPY dockerd_entrypoint.sh /opt/
RUN chmod +x /opt/dockerd_entrypoint.sh
ENV GOGC=off
ENTRYPOINT ["/opt/dockerd_entrypoint.sh"]
EOF

cat > exp_crossdomain/dockerd_entrypoint.sh << 'EOF'
#!/bin/sh
# Start dockerd with --debug so pprof is reachable via the API socket.
# GOGC=off (set in image) disables automatic GC; we force GC before
# warmup and again before action via curl to /debug/pprof/heap?gc=1.
nohup dockerd \
    --debug \
    --host=unix:///var/run/docker.sock \
    --tls=false \
    > /tmp/dockerd.log 2>&1 &

# Wait for daemon ready
for i in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then
        echo "dockerd ready"; break
    fi
    sleep 1
done

# Pre-pull alpine so action timing is not contaminated by network pull
docker pull alpine:latest >/dev/null 2>&1
echo "alpine pulled"

# Force a GC immediately so the warmup baseline starts from a clean heap.
# The dockerd pprof endpoint is on the same unix socket as the API.
# A GET to /debug/pprof/heap?gc=1 triggers runtime.GC() server-side.
curl --silent --unix-socket /var/run/docker.sock \
     "http://localhost/debug/pprof/heap?gc=1" > /dev/null || \
     echo "WARN: pprof GC trigger failed; continuing"
echo "GC forced; entering memdump runner"

# Run memdump (dockerd is the target). The runner will sleep WARMUP_S
# then call inject_action, which for dockerd actions triggers the
# docker CLI. Heap should stay stable across warmup because GOGC=off.
exec python3 /opt/memdump_runner.py
EOF
chmod +x exp_crossdomain/dockerd_entrypoint.sh

docker build -q -t echo-dind-img -f /tmp/Dockerfile.echo-dind exp_crossdomain/ > /dev/null

for rep in $(seq 1 $REPS); do
    EXP_NAME="dockerd_${ACTION}_rep${rep}_$(date +%s)"
    echo "=== Starting $EXP_NAME ==="
    docker rm -f "echo-dind-${rep}" 2>/dev/null || true
    docker run --rm \
        --name "echo-dind-${rep}" \
        --privileged \
        -v "$DATA_DIR":/data \
        -e EXP_NAME="$EXP_NAME" \
        -e TARGET_PROCESS=dockerd \
        -e ACTION_KIND="$ACTION" \
        -e WARMUP_S="$WARMUP_S" \
        -e OBSERVATION_S="$OBSERVATION_S" \
        -e DOCKER_TLS_CERTDIR="" \
        echo-dind-img 2>&1 | tee "$DATA_DIR/${EXP_NAME}.log"
    echo "=== $EXP_NAME done ==="
done
