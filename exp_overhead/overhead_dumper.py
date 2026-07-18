#!/usr/bin/env python3
"""
overhead_dumper — instrumented observer for the overhead experiment (R2#7/R4#4).

Runs INSIDE the target container (same placement as memdump_runner.py) and
executes the SAME capture path — find_target_pid / get_heap_ranges /
dump_pages / aggregate_features are imported from memdump_runner, not
reimplemented — so the measured cost is the cost of the real observer.

Self-reports, per iteration: dump wall latency and inter-dump interval
(effective cadence); at exit: CPU time, peak RSS, read/write bytes
(/proc/self via psutil), latency percentiles, and achieved-vs-nominal cadence.

Env:
  TARGET_PROCESS   process name to observe (redis-server | dockerd | ovs-vswitchd)
  DURATION_S       observation length (default 120)
  DUMP_INTERVAL_S  nominal cadence (default 0.5, same as measurement runs)
  OUT_JSON         where to write the summary (default /data/overhead_dumper.json)
"""
import json
import os
import sys
import time
from pathlib import Path
from statistics import mean

import psutil


def pct(xs, p):
    """Nearest-rank percentile on a list (stdlib-only; the baked image has no numpy)."""
    if not xs:
        return None
    s = sorted(xs)
    k = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
    return s[k]

os.environ.setdefault("EXP_NAME", "overhead_scratch")
sys.path.insert(0, "/opt")                      # container layout (memdump_runner at /opt)
sys.path.insert(0, str(Path(__file__).parent.parent / "exp_crossdomain"))  # repo layout
from memdump_runner import (find_target_pid, get_heap_ranges,   # noqa: E402
                            dump_pages, aggregate_features)

TARGET = os.environ.get("TARGET_PROCESS", "redis-server")
DURATION_S = float(os.environ.get("DURATION_S", "120"))
INTERVAL_S = float(os.environ.get("DUMP_INTERVAL_S", "0.5"))
OUT_JSON = Path(os.environ.get("OUT_JSON", "/data/overhead_dumper.json"))


def main():
    # Wait for the target daemon to be up AND to have a real heap. Under load the
    # daemon can be slow to start; until then find_target_pid may return nothing
    # (the shell-wrapper fallback is suppressed), so retry patiently and require a
    # non-trivial heap before measuring (a ~empty heap = wrong/half-started target).
    pid = None
    for _ in range(90):
        cand = find_target_pid(TARGET)
        if cand and len(get_heap_ranges(cand)) > 0 and \
                sum(e - s for s, e in get_heap_ranges(cand)) > 1_000_000:  # >1 MB heap
            pid = cand
            break
        time.sleep(1)
    if not pid:
        print(f"target {TARGET} not found with a non-trivial heap", file=sys.stderr)
        sys.exit(1)

    proc = psutil.Process()
    latencies, intervals, n_pages_seq = [], [], []
    prev_dump = None
    prev_start = None
    t_end = time.time() + DURATION_S
    while time.time() < t_end:
        t0 = time.time()
        if prev_start is not None:
            intervals.append(t0 - prev_start)
        prev_start = t0
        ranges = get_heap_ranges(pid)
        dump = dump_pages(pid, ranges)
        if prev_dump:
            aggregate_features(prev_dump, dump)   # same downstream cost as real runs
        prev_dump = dump
        t1 = time.time()
        latencies.append(t1 - t0)
        n_pages_seq.append(len(dump))
        time.sleep(max(0.0, INTERVAL_S - (t1 - t0)))

    cpu = proc.cpu_times()
    try:
        io = proc.io_counters()
        io_d = {"read_bytes": io.read_bytes, "write_bytes": io.write_bytes,
                "read_chars": getattr(io, "read_chars", None)}
    except Exception:
        io_d = {}
    overrun = (sum(1 for x in intervals if x > INTERVAL_S * 1.1) / len(intervals)
               if intervals else 0.0)
    out = {
        "target": TARGET, "target_pid": pid,
        "duration_s": DURATION_S, "nominal_interval_s": INTERVAL_S,
        "n_dumps": len(latencies),
        "dump_latency_s": {"p50": round(pct(latencies, 50) or 0.0, 4),
                           "p95": round(pct(latencies, 95) or 0.0, 4),
                           "max": round(max(latencies) if latencies else 0.0, 4)},
        "effective_interval_s": {"mean": round(mean(intervals), 4) if intervals else None,
                                 "p95": round(pct(intervals, 95) or 0.0, 4)},
        "cadence_overrun_pct": round(100.0 * overrun, 2),
        "pages_per_dump_mean": round(mean(n_pages_seq), 1) if n_pages_seq else 0.0,
        "observer_cpu_s": {"user": cpu.user, "system": cpu.system},
        "observer_cpu_pct_of_wall": round(100.0 * (cpu.user + cpu.system) / DURATION_S, 2),
        "observer_rss_bytes": proc.memory_info().rss,
        "observer_io": io_d,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
