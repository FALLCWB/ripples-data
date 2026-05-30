#!/usr/bin/env python3
"""Generic memory-echo experiment runner for cross-domain validation.

Runs INSIDE a docker container that has access to /proc of the target
process (either same container or via --pid namespace sharing).

Architecture:
  - Target software (redis-server / nginx) starts and idles
  - Warmup phase: dump memory periodically, no external actions
  - Action phase: inject a controlled action, record timestamp
  - Observation phase: continue dumping for AFTERMATH_S seconds

Output (to /data/<exp_name>/):
  - features.csv: per-dump aggregate features (timestamp, n_changed_pages,
                  vol_sum, entropy_mean, ...)
  - events.json: action timestamps + metadata
  - markers.json: warmup_start, action_ts, observation_end

The downstream analyzer (analyze_crossdomain.py, run on host) reads these
and applies the 6-category framework.
"""
import json
import os
import subprocess
import sys
import time
import hashlib
from pathlib import Path
import psutil

PAGE_SIZE = 4096
WARMUP_S = int(os.environ.get("WARMUP_S", "300"))
OBSERVATION_S = int(os.environ.get("OBSERVATION_S", "300"))
DUMP_INTERVAL_S = float(os.environ.get("DUMP_INTERVAL_S", "0.5"))
TARGET_PROCESS = os.environ.get("TARGET_PROCESS", "redis-server")
EXP_NAME = os.environ.get("EXP_NAME", "default")
ACTION_KIND = os.environ.get("ACTION_KIND", "noop")

OUT_DIR = Path(f"/data/{EXP_NAME}")
OUT_DIR.mkdir(parents=True, exist_ok=True)
FEATURES_PATH = OUT_DIR / "features.csv"
EVENTS_PATH = OUT_DIR / "events.json"
MARKERS_PATH = OUT_DIR / "markers.json"


def find_target_pid(name):
    """Return PID of first process matching name."""
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if p.info['name'] == name or (p.info['cmdline'] and name in ' '.join(p.info['cmdline'])):
                return p.info['pid']
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def get_heap_ranges(pid):
    """Return [(start, end), ...] for writable anonymous VMAs of pid.

    Captures BOTH [heap] (sbrk) and anonymous mmap (jemalloc arenas).
    jemalloc-based processes like redis allocate via mmap, not sbrk, so
    looking only at [heap] would miss almost all real allocations.
    """
    ranges = []
    cap_bytes = int(os.environ.get("MEM_CAP_MIB", "256")) * 1024 * 1024
    total = 0
    try:
        with open(f"/proc/{pid}/maps") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 5:
                    continue
                addr_range, perms = parts[0], parts[1]
                # Writable, private, anonymous (no backing file or [heap])
                if 'w' in perms and 'p' in perms:
                    # Anonymous: 6th field is missing or [heap]
                    path = parts[5] if len(parts) >= 6 else ""
                    if path == "" or path == "[heap]":
                        start_str, end_str = addr_range.split('-')
                        start, end = int(start_str, 16), int(end_str, 16)
                        size = end - start
                        # Skip huge regions (likely stack guards or sparse mappings)
                        if size > 64 * 1024 * 1024:  # 64 MiB cap per region
                            continue
                        if total + size > cap_bytes:
                            break
                        ranges.append((start, end))
                        total += size
    except FileNotFoundError:
        pass
    return ranges


def dump_pages(pid, ranges):
    """Return dict {page_addr: (sha1_digest, raw_bytes)} for all readable pages.

    Pages are keyed by ABSOLUTE virtual address (start of page within the
    process address space), NOT by a positional index. This is critical:
    positional indexing aliases pages across iterations when the heap or
    mmap arenas grow/shrink, so iteration N's "page 42" may correspond to
    a completely different region than iteration N-1's "page 42". Address
    keying is stable as long as the VMA stays mapped.
    """
    pages = {}
    try:
        fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
    except (PermissionError, FileNotFoundError):
        return pages
    try:
        for start, end in ranges:
            offset = start
            while offset < end:
                try:
                    os.lseek(fd, offset, os.SEEK_SET)
                    data = os.read(fd, PAGE_SIZE)
                    if data:
                        pages[offset] = (hashlib.sha1(data).digest(), data)
                except OSError:
                    pass
                offset += PAGE_SIZE
    finally:
        os.close(fd)
    return pages


def aggregate_features(prev_dump, curr_dump):
    """Compute aggregate features for current dump vs previous.

    Each dump is a dict {addr: (sha1, raw_bytes)}. A page is "changed"
    when its address exists in both dumps and the hashes differ. Newly
    appeared addresses (heap growth) are NOT counted as changes — they
    are unobservable in the prior state, so attribution is ambiguous.

    Returns dict with: n_changed_pages, change_vol_bytes, entropy_mean,
    max_page_addr_changed.
    """
    if not prev_dump or not curr_dump:
        return None
    from collections import Counter
    import math
    changed_addrs = [a for a, (h, _) in curr_dump.items()
                     if a in prev_dump and prev_dump[a][0] != h]
    if not changed_addrs:
        return {"n_changed_pages": 0, "change_vol_bytes": 0,
                "entropy_mean": 0.0, "max_page_addr_changed": 0}
    vol = 0
    entropies = []
    for a in changed_addrs:
        prev_bytes = prev_dump[a][1]
        curr_bytes = curr_dump[a][1]
        min_len = min(len(prev_bytes), len(curr_bytes))
        diff_bytes = [curr_bytes[i] for i in range(min_len)
                      if prev_bytes[i] != curr_bytes[i]]
        vol += len(diff_bytes)
        if diff_bytes:
            c = Counter(diff_bytes)
            total = sum(c.values())
            ent = -sum((v/total) * math.log2(v/total) for v in c.values())
            entropies.append(ent)
    return {
        "n_changed_pages": len(changed_addrs),
        "change_vol_bytes": vol,
        "entropy_mean": sum(entropies)/len(entropies) if entropies else 0.0,
        "max_page_addr_changed": max(changed_addrs),
    }


def inject_action(kind):
    """Run a target-specific action. Returns timestamp injected."""
    ts = time.time()
    if kind == "redis_set_1":
        subprocess.run(["redis-cli", "SET", "k1", "v1"], check=False, capture_output=True)
    elif kind == "redis_mset_100":
        kv = []
        for i in range(100):
            kv.append(f"k{i}"); kv.append(f"v{i}")
        subprocess.run(["redis-cli", "MSET"] + kv, check=False, capture_output=True)
    elif kind == "redis_flushdb":
        subprocess.run(["redis-cli", "FLUSHDB"], check=False, capture_output=True)
    elif kind == "nginx_curl_1":
        subprocess.run(["curl", "-s", "-o", "/dev/null", "http://localhost/"],
                       check=False, capture_output=True)
    elif kind == "nginx_burst_100":
        subprocess.run(["ab", "-q", "-n", "100", "-c", "10", "http://localhost/"],
                       check=False, capture_output=True)
    elif kind == "nginx_reload":
        master_pid = find_target_pid("nginx")
        if master_pid:
            os.kill(master_pid, 1)  # SIGHUP
    elif kind.startswith("python_"):
        wpid = int(os.environ.get("WORKLOAD_PID", "0"))
        if wpid:
            os.kill(wpid, 10)  # SIGUSR1
    elif kind == "docker_inspect":
        # Truly-small surface control: pure readback of daemon state.
        # No container spawned, no image mounted, no cgroup created.
        # Used to show that even readback produces a ripple in a stateful
        # daemon (string allocation, internal cache touches).
        subprocess.run(["docker", "version"],
                       check=False, capture_output=True, timeout=10)
    elif kind == "docker_run_1":
        subprocess.run(["docker", "run", "-d", "--rm", "alpine:latest", "sleep", "60"],
                       check=False, capture_output=True, timeout=30)
    elif kind == "docker_run_10":
        for _ in range(10):
            subprocess.run(["docker", "run", "-d", "--rm", "alpine:latest", "sleep", "60"],
                           check=False, capture_output=True, timeout=30)
    elif kind == "docker_run_50":
        for _ in range(50):
            subprocess.run(["docker", "run", "-d", "--rm", "alpine:latest", "sleep", "60"],
                           check=False, capture_output=True, timeout=30)
    elif kind == "noop":
        pass
    else:
        print(f"unknown action: {kind}")
    return ts


def main():
    print(f"[{time.strftime('%H:%M:%S')}] runner starting", flush=True)
    print(f"  target={TARGET_PROCESS} action={ACTION_KIND} exp={EXP_NAME}", flush=True)

    # Find target PID — prefer WORKLOAD_PID env var if set (Python case)
    workload_pid_env = int(os.environ.get("WORKLOAD_PID", "0"))
    if workload_pid_env:
        pid = workload_pid_env
        print(f"  target_pid={pid} (from WORKLOAD_PID env)", flush=True)
    else:
        deadline = time.time() + 30
        pid = None
        while time.time() < deadline:
            pid = find_target_pid(TARGET_PROCESS)
            if pid:
                break
            time.sleep(1)
        if not pid:
            print(f"FATAL: target process '{TARGET_PROCESS}' not found", flush=True)
            sys.exit(1)
        print(f"  target_pid={pid}", flush=True)

    # Initial heap ranges
    ranges = get_heap_ranges(pid)
    if not ranges:
        print(f"FATAL: no [heap] VMA for pid={pid}", flush=True)
        sys.exit(1)
    print(f"  heap_range={ranges[0]} ({(ranges[0][1]-ranges[0][0])/1024:.0f} KiB)", flush=True)

    # Open features.csv
    fh = open(FEATURES_PATH, "w")
    fh.write("ts,n_changed_pages,change_vol_bytes,entropy_mean,max_page_addr_changed\n")

    events = []
    warmup_start = time.time()
    action_ts = None
    observation_end = None

    print(f"[{time.strftime('%H:%M:%S')}] warmup {WARMUP_S}s", flush=True)
    prev_dump = None
    next_dump = time.time()
    end_phase = warmup_start + WARMUP_S
    in_warmup = True

    while True:
        now = time.time()
        if in_warmup and now >= end_phase:
            in_warmup = False
            print(f"[{time.strftime('%H:%M:%S')}] injecting action: {ACTION_KIND}", flush=True)
            action_ts = inject_action(ACTION_KIND)
            events.append({"ts": action_ts, "action": "inject_action",
                           "kind": ACTION_KIND})
            end_phase = action_ts + OBSERVATION_S
            print(f"[{time.strftime('%H:%M:%S')}] observation {OBSERVATION_S}s", flush=True)
        if not in_warmup and now >= end_phase:
            observation_end = now
            break
        if now >= next_dump:
            ranges = get_heap_ranges(pid)
            if not ranges:
                time.sleep(0.1)
                continue
            curr_dump = dump_pages(pid, ranges)
            if prev_dump:
                feats = aggregate_features(prev_dump, curr_dump)
                if feats is not None:
                    fh.write(f"{now},{feats['n_changed_pages']},{feats['change_vol_bytes']},"
                             f"{feats['entropy_mean']:.4f},{feats['max_page_addr_changed']}\n")
                    fh.flush()
            prev_dump = curr_dump
            next_dump = now + DUMP_INTERVAL_S
        time.sleep(0.05)

    fh.close()

    markers = {
        "warmup_start_ts": warmup_start,
        "action_ts": action_ts,
        "observation_end_ts": observation_end,
        "target_process": TARGET_PROCESS,
        "action_kind": ACTION_KIND,
        "exp_name": EXP_NAME,
        "warmup_s": WARMUP_S,
        "observation_s": OBSERVATION_S,
        "dump_interval_s": DUMP_INTERVAL_S,
        "heap_initial_kib": (ranges[0][1] - ranges[0][0]) / 1024,
    }
    MARKERS_PATH.write_text(json.dumps(markers, indent=2))
    EVENTS_PATH.write_text(json.dumps(events, indent=2))
    print(f"[{time.strftime('%H:%M:%S')}] DONE -> /data/{EXP_NAME}/", flush=True)


if __name__ == "__main__":
    main()
