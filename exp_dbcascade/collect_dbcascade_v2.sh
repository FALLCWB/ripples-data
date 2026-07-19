#!/bin/bash
# Collect reps with jittered params so the ordering ablation carries variance/CI.
OUT="$1"; N="${2:-18}"
mkdir -p "$OUT"
LAGS=(2 4 6); STG=(4 6 8); INT=(0.8 1.0 1.2)
for i in $(seq 1 "$N"); do
  L=${LAGS[$((i % 3))]}; S=${STG[$(((i/3) % 3))]}; IV=${INT[$((i % 3))]}
  echo "=== rep $i (lag=$L stages=$S interval=$IV) ==="
  WARMUP_S=30 OBSERVATION_S=40 FETCH_LAG_S="$L" CASCADE_STAGES="$S" STAGE_INTERVAL_S="$IV" \
    ADMIN_GAP_S=4 python3 run_dbcascade.py "$OUT/rep$i"
  sleep 2
done
echo ALLDONE
