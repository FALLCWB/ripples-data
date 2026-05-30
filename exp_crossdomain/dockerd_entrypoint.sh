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
