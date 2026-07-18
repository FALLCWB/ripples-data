#!/bin/bash
# Overhead experiment (reviewers R2#7, R4#4, R1#2): quantify the observer's
# cost and its impact on the target daemon, with-vs-without dumper arms.
#
# Runs ON the lab VM (FE-L-1), from the repo root, AFTER the main sweep and
# the G/H collections (never concurrently with measurement runs).
#
#   TARGETS x ARMS x REPS, arms alternated (with, without, with, ...) to
#   spread thermal/background drift across both arms.
#
# Targets and daemon-impact benchmarks:
#   redis   : redis-benchmark -t set,get -n 100000 (throughput ops/s)
#   dockerd : wall time of 20x `docker run --rm alpine true` (dind, GOGC=off,
#             same arm as the measurement runs)
#   ovs     : NOT here — OvS overhead runs through the lab harness (mission.py
#             dumper toggle); see scripts/lab.py. TODO-at-deploy: verify the
#             switch image exposes a dumper on/off env before running.
#
# Observer instrumentation: exp_overhead/overhead_dumper.py (imports the real
# capture path from memdump_runner.py; self-reports CPU/RSS/io/latency).
#
# Usage: GOGC_MODE=off REPS=5 ./exp_overhead/run_overhead.sh redis
set -euo pipefail
TARGET="${1:-redis}"
REPS="${REPS:-5}"
DURATION_S="${DURATION_S:-120}"
DATA_DIR="$(cd "$(dirname "$0")/.." && pwd)/data/overhead"
mkdir -p "$DATA_DIR"
cd "$(dirname "$0")/.."

bench_redis() {           # $1 = container name -> prints "SET_ops/s GET_ops/s"
    # --csv is machine-readable: "SET","143266.48"  (robust vs the -q progress lines)
    docker exec "$1" redis-benchmark -t set,get -n 100000 --csv 2>/dev/null \
        | awk -F',' '/"SET"|"GET"/ {gsub(/"/,"",$2); printf "%s ", $2}'
}

bench_dockerd() {         # $1 = container name -> prints total wall seconds
    docker exec "$1" sh -c '
        start=$(date +%s.%N)
        i=0; while [ $i -lt 20 ]; do docker run --rm alpine true >/dev/null 2>&1; i=$((i+1)); done
        end=$(date +%s.%N)
        echo "$end $start" | awk "{print \$1-\$2}"'
}

run_rep() {               # $1 target  $2 arm(with|without)  $3 rep
    local target="$1" arm="$2" rep="$3"
    local name="oh-${target}-${arm}-${rep}"
    local exp="overhead_${target}_${arm}_rep${rep}_$(date +%s)"
    echo "=== $exp ==="
    docker rm -f "$name" 2>/dev/null || true

    if [ "$target" = "redis" ]; then
        # echo-redis image has python+psutil+runner baked in; override the
        # entrypoint so ONLY redis runs, and we control the dumper explicitly.
        # SYS_PTRACE: in the May runs the dumper was redis's PARENT (ptrace allowed
        # parent->child), so it read /proc/pid/mem without a cap. Here the dumper is
        # a docker-exec sibling of redis (PID 1), so it needs the cap to read the
        # target's memory. The cap only enables the read; it does not affect the
        # measured observer cost.
        docker run -d --name "$name" --cap-add SYS_PTRACE \
            -v "$DATA_DIR":/data \
            -v "$(pwd)/exp_overhead/overhead_dumper.py":/opt/overhead_dumper.py:ro \
            --entrypoint redis-server \
            echo-redis-par-img > /dev/null
        sleep 3
        local proc="redis-server"
    elif [ "$target" = "dockerd" ]; then
        docker run -d --name "$name" --privileged \
            -v "$DATA_DIR":/data \
            -v "$(pwd)/exp_overhead/overhead_dumper.py":/opt/overhead_dumper.py:ro \
            -e DOCKER_TLS_CERTDIR="" -e GOGC="${GOGC_MODE:-off}" \
            --entrypoint sh \
            echo-dind-par-img -c 'nohup dockerd --debug --host=unix:///var/run/docker.sock --tls=false >/tmp/d.log 2>&1 & sleep 5; docker pull alpine:latest >/dev/null 2>&1; sleep infinity' > /dev/null
        sleep 15
        local proc="dockerd"
    else
        echo "unknown target $target"; exit 1
    fi

    local dumper_pid=""
    if [ "$arm" = "with" ]; then
        docker exec -d \
            -e TARGET_PROCESS="$proc" -e DURATION_S="$DURATION_S" \
            -e OUT_JSON="/data/${exp}_dumper.json" \
            "$name" python3 /opt/overhead_dumper.py
        sleep 5   # let the dumper reach steady state before benchmarking
    fi

    local t0 t1 bench
    t0=$(date +%s.%N)
    if [ "$target" = "redis" ]; then bench=$(bench_redis "$name"); else bench=$(bench_dockerd "$name"); fi
    t1=$(date +%s.%N)

    if [ "$arm" = "with" ]; then
        # wait for the dumper to finish its window and write its summary
        local waited=0
        while [ ! -f "$DATA_DIR/${exp}_dumper.json" ] && [ $waited -lt $((${DURATION_S%.*} + 60)) ]; do
            sleep 5; waited=$((waited + 5))
        done
    fi
    docker rm -f "$name" > /dev/null

    python3 - "$DATA_DIR/${exp}.json" << PYEOF
import json, sys
json.dump({"exp": "$exp", "target": "$target", "arm": "$arm", "rep": $rep,
           "bench_result": "$bench".split(),
           "bench_wall_s": $t1 - $t0,
           "duration_s": $DURATION_S}, open(sys.argv[1], "w"), indent=1)
PYEOF
    echo "  bench=[$bench] wall=$(echo "$t1 $t0" | awk '{print $1-$2}')s"
}

for rep in $(seq 1 "$REPS"); do
    run_rep "$TARGET" with "$rep"
    run_rep "$TARGET" without "$rep"
done
echo "done. results in $DATA_DIR (pair *_dumper.json with *.json by exp name)"
