#!/bin/bash
# Run CPython cross-domain echo experiment.
# Action types:
#   python_dict_10   — d={k:k*2 for k in range(10)}     (small surface)
#   python_dict_1k   — d={k:k*2 for k in range(1000)}   (medium)
#   python_list_100k — l=[i for i in range(100000)]; l.sort()  (large)
set -euo pipefail
ACTION="${1:-python_dict_10}"
REPS="${2:-3}"
WARMUP_S="${WARMUP_S:-180}"
OBSERVATION_S="${OBSERVATION_S:-180}"
DATA_DIR="$(cd "$(dirname "$0")/.." && pwd)/data/crossdomain"
mkdir -p "$DATA_DIR"
cd "$(dirname "$0")/.."

# Build image once: python + psutil + the workload script
cat > /tmp/Dockerfile.echo-python << 'EOF'
FROM python:3.12-alpine
RUN pip install --no-cache-dir psutil
COPY memdump_runner.py /opt/
COPY python_workload.py /opt/
COPY python_entrypoint.sh /opt/
RUN chmod +x /opt/python_entrypoint.sh
ENTRYPOINT ["/opt/python_entrypoint.sh"]
EOF

cat > exp_crossdomain/python_workload.py << 'EOF'
"""Long-running Python process whose memory we monitor.
Sleeps, then performs an action on signal, then sleeps again.
We use SIGUSR1 to trigger the action so the parent (memdump runner)
can drive timing precisely.
"""
import os, sys, time, signal, gc
ACTION = os.environ.get("ACTION_KIND", "python_dict_10")
gc.disable()  # we want allocator activity to be observable, not GC'd away
# Pre-warm: allocate a baseline object set so heap isn't a single page
warmup_data = []
for _ in range(200):
    warmup_data.append(bytes(1024))  # 200 x 1 KiB = 200 KiB baseline
sys.stdout.write(f"workload ready pid={os.getpid()}\n"); sys.stdout.flush()

action_done = False

def do_action(*_):
    global action_done
    if action_done:
        return
    action_done = True
    if ACTION == "python_dict_10":
        d = {k: k*2 for k in range(10)}
        sys.stdout.write(f"dict_10 len={len(d)}\n")
    elif ACTION == "python_dict_1k":
        d = {k: k*2 for k in range(1000)}
        sys.stdout.write(f"dict_1k len={len(d)}\n")
    elif ACTION == "python_list_100k":
        l = [i for i in range(100000)]
        l.sort(reverse=True)
        sys.stdout.write(f"list_100k len={len(l)}\n")
    sys.stdout.flush()
    # Don't free — let echo persist as long as possible
    globals()["_kept"] = locals()

signal.signal(signal.SIGUSR1, do_action)

while True:
    time.sleep(1)
EOF

cat > exp_crossdomain/python_entrypoint.sh << 'EOF'
#!/bin/sh
# Start workload in background
python3 /opt/python_workload.py &
WORKLOAD_PID=$!
sleep 1
# Tell the memdump runner about the workload pid and action timing
export TARGET_PROCESS=python3
export WORKLOAD_PID
exec python3 /opt/memdump_runner.py
EOF
chmod +x exp_crossdomain/python_entrypoint.sh

docker build -q -t echo-python-img -f /tmp/Dockerfile.echo-python exp_crossdomain/ > /dev/null

for rep in $(seq 1 $REPS); do
    EXP_NAME="python_${ACTION}_rep${rep}_$(date +%s)"
    echo "=== Starting $EXP_NAME ==="
    docker rm -f "echo-python-${rep}" 2>/dev/null || true
    docker run --rm \
        --name "echo-python-${rep}" \
        -v "$DATA_DIR":/data \
        -e EXP_NAME="$EXP_NAME" \
        -e ACTION_KIND="$ACTION" \
        -e WARMUP_S="$WARMUP_S" \
        -e OBSERVATION_S="$OBSERVATION_S" \
        echo-python-img 2>&1 | tee "$DATA_DIR/${EXP_NAME}.log"
    echo "=== $EXP_NAME done ==="
done
