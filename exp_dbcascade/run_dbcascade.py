#!/usr/bin/env python3
"""One repetition of the DB-cascade ground-truth ablation (R2.12).

Timeline (all after a quiet warmup):
  t0            : SIGUSR1 -> INGEST (induced action) -> delayed reactive cascade.
  t_leg         : SIGUSR2 -> ADMIN (independent direct action), after the cascade.
Ground truth: cascade events are the ingest's reaction (Induced-cascade); the
admin event is a direct effect of an independent action (Direct-anchor).

Reuses the paper's capture path (get_heap_ranges / dump_pages / aggregate_features).
Crash-safe teardown; records both action times; leaves an ingest_ok flag so the
eval can drop a rep whose ingest failed.
"""
import json
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/opt/ripples-exp/exp_crossdomain")
from memdump_runner import get_heap_ranges, dump_pages, aggregate_features  # noqa: E402

HERE = Path(__file__).resolve().parent
REP_DIR = Path(sys.argv[1])
WARMUP_S = float(os.environ.get("WARMUP_S", "60"))
OBSERVATION_S = float(os.environ.get("OBSERVATION_S", "70"))
LAG_S = float(os.environ.get("FETCH_LAG_S", "4"))
STAGES = int(os.environ.get("CASCADE_STAGES", "6"))
STAGE_INTERVAL_S = float(os.environ.get("STAGE_INTERVAL_S", "1.0"))
ADMIN_GAP_S = float(os.environ.get("ADMIN_GAP_S", "4"))  # admin fires this long after the cascade ends
DUMP_INTERVAL_S = 0.5
AUDIT_PATH = str(REP_DIR / "audit.jsonl")

REP_DIR.mkdir(parents=True, exist_ok=True)
env = dict(os.environ, FETCH_LAG_S=str(LAG_S), CASCADE_STAGES=str(STAGES),
           STAGE_INTERVAL_S=str(STAGE_INTERVAL_S), AUDIT_PATH=AUDIT_PATH)

mock = subprocess.Popen([sys.executable, str(HERE / "mock_weather.py")])
wl = None
fh = None
pid = None
try:
    time.sleep(1.5)
    wl = subprocess.Popen([sys.executable, str(HERE / "enrichment_workload.py")],
                          env=env, stdout=subprocess.PIPE, text=True, bufsize=1)
    # Bounded wait for readiness: a stalled Postgres connect must not hang the run.
    deadline = time.time() + WARMUP_S
    while pid is None and time.time() < deadline:
        if wl.poll() is not None:
            raise RuntimeError("workload exited before becoming ready")
        r, _, _ = select.select([wl.stdout], [], [], 1.0)
        if not r:
            continue
        line = wl.stdout.readline()
        if not line:
            raise RuntimeError("workload stdout closed before ready")
        sys.stdout.write("[wl] " + line)
        if line.startswith("workload ready pid="):
            pid = int(line.strip().split("=")[1])
    if not pid:
        raise RuntimeError("workload did not become ready within WARMUP_S")

    fh = open(REP_DIR / "features.csv", "w")
    fh.write("ts,n_changed_pages,change_vol_bytes,entropy_mean,max_page_addr_changed\n")

    warmup_start = time.time()
    action_ts = legit_ts = observation_end = None
    prev_dump = None
    next_dump = warmup_start
    phase = "warmup"
    end_phase = warmup_start + WARMUP_S
    cascade_end = None
    legit_deadline = None

    while True:
        now = time.time()
        if phase == "warmup" and now >= end_phase:
            phase = "observe"
            action_ts = time.time()
            os.kill(pid, signal.SIGUSR1)   # INGEST (induced)
            cascade_end = action_ts + LAG_S + STAGES * STAGE_INTERVAL_S
            legit_deadline = cascade_end + ADMIN_GAP_S
            end_phase = action_ts + OBSERVATION_S
            print(f"[{time.strftime('%H:%M:%S')}] ingest at {action_ts:.3f}", flush=True)
        if phase == "observe" and legit_ts is None and now >= legit_deadline:
            legit_ts = time.time()
            os.kill(pid, signal.SIGUSR2)   # ADMIN (independent direct)
            print(f"[{time.strftime('%H:%M:%S')}] admin at {legit_ts:.3f}", flush=True)
        if phase == "observe" and now >= end_phase:
            observation_end = now
            break
        if now >= next_dump:
            ranges = get_heap_ranges(pid)
            if ranges:
                curr = dump_pages(pid, ranges)
                if prev_dump:
                    f = aggregate_features(prev_dump, curr)
                    if f is not None:
                        fh.write(f"{now},{f['n_changed_pages']},{f['change_vol_bytes']},"
                                 f"{f['entropy_mean']:.4f},{f['max_page_addr_changed']}\n")
                        fh.flush()
                prev_dump = curr
            next_dump = now + DUMP_INTERVAL_S
        time.sleep(0.05)

    fh.close(); fh = None
    audit_lines = [json.loads(l) for l in open(AUDIT_PATH)] if os.path.exists(AUDIT_PATH) else []
    n_reactive = sum(a["role"] == "reactive" for a in audit_lines)
    ingest_ok = (any(a["role"] == "induced" for a in audit_lines)
                 and n_reactive == STAGES              # full cascade, not a truncated one
                 and not any(a["role"] == "failed" for a in audit_lines)
                 and any(a["role"] == "legit" for a in audit_lines))
    (REP_DIR / "markers.json").write_text(json.dumps({
        "warmup_start_ts": warmup_start, "action_ts": action_ts, "legit_ts": legit_ts,
        "observation_end_ts": observation_end, "target_process": "enrichment_workload",
        "fetch_lag_s": LAG_S, "stages": STAGES, "stage_interval_s": STAGE_INTERVAL_S,
        "ingest_ok": ingest_ok}))
    (REP_DIR / "events.json").write_text(json.dumps([
        {"ts": action_ts, "action": "inject_action", "kind": "db_ingest"},
        {"ts": legit_ts, "action": "legit_action", "kind": "db_admin"}]))
    print(f"DONE {REP_DIR} ingest_ok={ingest_ok} audit_lines={len(audit_lines)}", flush=True)
finally:
    if fh:
        try: fh.close()
        except Exception: pass
    if wl:                                              # terminate via the child handle (race-safe, no raw-PID reuse)
        try: wl.terminate()
        except Exception: pass
        try: wl.wait(timeout=5)
        except Exception:
            try: wl.kill()
            except Exception: pass
    mock.terminate()
    try: mock.wait(timeout=5)
    except Exception: mock.kill()
