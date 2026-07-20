#!/usr/bin/env python3
"""
revision_numbers — recompute and PERSIST every number introduced in the
resubmission response that was not already backed by a committed artifact
(R2.10 default-GC, R2.11 calibrated ripple-presence, R3.1 readback amplification,
R4.2 shifted-anchor spurious attribution, R4.6 robustness signature). Writes revision_numbers.json so each reported value is persisted with its source. The raw input corpora below live on the collection host and are too large to redistribute (multi-GB, live memory); the released package ships revision_numbers.json with the computed values rather than these raw traces.

Data locations (read-only):
  ~/research/ripples-recollection/gcdefault      GOGC=100 dockerd (R2.10)
  ~/research/ripples-data/data/crossdomain       preserved GOGC=off dockerd (R2.11, R3.1)
  ~/research/ripples-recollection/snapshots      dense OvS induced (R4.2)
  ~/research/ripples-recollection/r46_server     redis6/Debian (R4.6)
  ~/research/ripples-recollection/r46_local      redis7/this-host (R4.6)
"""
import csv, glob, json, os
from pathlib import Path
import numpy as np
from scipy.stats import spearmanr

H = os.path.expanduser("~")
PKG = Path(__file__).resolve().parent.parent  # ripples-data root
GC = f"{H}/research/ripples-recollection/gcdefault"
CD = str(PKG / "data/crossdomain")
SNAP = f"{H}/research/ripples-recollection/snapshots"
R46S = f"{H}/research/ripples-recollection/r46_server"
R46L = f"{H}/research/ripples-recollection/r46_local"


def _rows(d, col):
    """(ts, value at column `col`) from a rep's features CSV, action_ts from markers."""
    try:
        a = json.load(open(f"{d}/markers.json"))["action_ts"]
    except Exception:
        return None
    f = glob.glob(f"{d}/features*.csv")
    if not f:
        return None
    out = []
    for r in csv.reader(open(f[0])):
        if not r or not r[0].replace(".", "", 1).isdigit():
            continue
        try:
            out.append((float(r[0]), float(r[col])))
        except (IndexError, ValueError):
            continue
    return a, out


def amp(d, col=1):
    L = _rows(d, col)
    if not L:
        return None
    a, rows = L
    w = [v for ts, v in rows if ts < a]
    p = [v for ts, v in rows if ts >= a]
    if len(w) < 5 or not p:
        return None
    return max(p) / (np.mean(w) or 1e-9)


def r2_10_gcdefault():
    SURF = {"docker_inspect": 0, "docker_run_1": 1, "docker_run_10": 10}
    surf, amps, amean = [], [], {}
    for act, s in SURF.items():
        vs = [v for d in sorted(glob.glob(f"{GC}/dockerd_gcdefault_{act}_rep*"))
              if os.path.isdir(d) and (v := amp(d)) is not None]
        amean[act] = round(float(np.mean(vs)), 2)
        surf += [s] * len(vs); amps += vs
    rho = float(spearmanr(surf, amps).statistic)
    rng = np.random.default_rng(42)
    from collections import defaultdict
    idx = defaultdict(list)
    for i, s in enumerate(surf):
        idx[s].append(i)
    rhos = []
    for _ in range(10000):
        take = []
        for s, ii in idx.items():
            take += list(rng.choice(ii, len(ii), replace=True))
        r = spearmanr([surf[i] for i in take], [amps[i] for i in take]).statistic
        if not np.isnan(r):
            rhos.append(r)
    return {"spearman_rho": round(rho, 3),
            "ci95": [round(float(np.percentile(rhos, 2.5)), 3),
                     round(float(np.percentile(rhos, 97.5)), 3)],
            "amplification_mean": amean, "n": len(amps)}


def r2_11_presence():
    AFT = 180.0
    null, reps = [], {}
    for act in ["docker_inspect", "docker_run_1", "docker_run_10", "docker_run_50"]:
        reps[act] = []
        for d in sorted(glob.glob(f"{CD}/dockerd_{act}_rep*")):
            L = _rows(d, 2)  # change_vol_bytes
            if not L:
                continue
            a, rows = L
            W = [(ts, cv) for ts, cv in rows if ts < a]
            P = [(ts, cv) for ts, cv in rows if ts >= a]
            if len(W) < 10 or not P:
                continue
            thr = np.percentile([cv for _, cv in W], 95)
            reps[act].append(sum(1 for ts, cv in P if a <= ts <= a + AFT and cv > thr))
            wmax = max(ts for ts, _ in W)
            null.append(sum(1 for ts, cv in W if ts >= wmax - AFT and cv > thr))
    null = np.array(null)
    q95 = float(np.percentile(null, 95))
    out = {"null_draws": len(null), "null_q95": q95,
           "null_fp_rate": round(float(np.mean(null > q95)), 3), "presence": {}}
    for act, cts in reps.items():
        pres = sum(1 for c in cts if c > q95)
        out["presence"][act] = {"n": len(cts), "present": pres,
                                "rate": round(pres / len(cts), 3)}
    return out


def r3_1_readback():
    for row in csv.DictReader(open(PKG / "data/processed/crossdomain_summary.csv")):
        if row["system"] == "Dockerd" and "readback" in row["action"]:
            return {"table2_gogc_off": round(float(row["amplification"]), 2),
                    "baseline": round(float(row["baseline_pages"]), 3),
                    "peak": round(float(row["peak_pages_mean"]), 1)}
    return {}


def r4_2_shifted():
    DELTA = 300.0
    real, shift = [], []
    for pref in ["D_flush", "E_single_rule", "F_burst"]:
        for d in sorted(glob.glob(f"{SNAP}/{pref}_rep*")):
            if not os.path.isdir(d):
                continue
            mk = json.load(open(f"{d}/markers.json"))
            p1s = mk["warmup_start_ts"]
            ev = json.load(open(f"{d}/events.json"))
            a = next((float(e["ts"]) for e in ev
                      if e.get("action") in ("inject_action", "inject_attack")), None)
            f = glob.glob(f"{d}/features_switch1_*_post_action.csv") or glob.glob(f"{d}/features_switch1_*.csv")
            if a is None or not f:
                continue
            from collections import defaultdict
            agg = defaultdict(float)
            for r in csv.reader(open(f[-1])):
                if len(r) < 6:
                    continue
                try:
                    ts, cv = float(r[0]), float(r[5])
                except ValueError:
                    continue
                if cv > 0:
                    agg[ts] += cv
            sig = sorted(agg.items())
            pre = [v for ts, v in sig if ts < a]
            if len(pre) < 10:
                continue
            thr = np.percentile(pre, 95)
            real.append(sum(1 for ts, v in sig if a <= ts <= a + DELTA and v > thr))
            sa = p1s + 30.0
            shift.append(sum(1 for ts, v in sig if sa <= ts <= sa + DELTA and v > thr))
    real, shift = np.array(real), np.array(shift)
    return {"n": len(real), "real_mean": round(float(real.mean()), 1),
            "shifted_mean": round(float(shift.mean()), 1),
            "spurious_rate": round(float(shift.sum() / max(real.sum(), 1)), 4)}


def _within(base, prefix, action):
    BUCKET, WIN = 5.0, 200.0
    vs = []
    for d in sorted(glob.glob(f"{base}/{prefix}_{action}_rep*")):
        if not os.path.isdir(d):
            continue
        L = _rows(d, 2)
        if not L:
            continue
        a, rows = L
        pre = [v for ts, v in rows if ts < a]
        if len(pre) < 5:
            continue
        thr = np.percentile(pre, 95)
        n = int(np.ceil(WIN / BUCKET)); vec = np.zeros(n)
        for ts, v in rows:
            if a <= ts <= a + WIN and v - thr > 0:
                vec[min(int((ts - a) / BUCKET), n - 1)] += v - thr
        if vec.sum() > 0:
            vs.append(vec)
    cos = []
    for i in range(len(vs)):
        for j in range(i + 1, len(vs)):
            na, nb = np.linalg.norm(vs[i]), np.linalg.norm(vs[j])
            if na and nb:
                cos.append(float(vs[i] @ vs[j] / (na * nb)))
    return round(float(np.mean(cos)), 3) if cos else None


def r4_6_robustness():
    out = {}
    for a in ["redis_set_1", "redis_mset_100", "redis_flushdb"]:
        out[a] = {"redis6_debian": _within(R46S, "redis6deb", a),
                  "redis7_localhost": _within(R46L, "redis7local", a)}
    return out


if __name__ == "__main__":
    result = {
        "R2.10_default_gc": r2_10_gcdefault(),
        "R2.11_ripple_presence_calibrated": r2_11_presence(),
        "R3.1_readback_amplification": r3_1_readback(),
        "R4.2_shifted_anchor": r4_2_shifted(),
        "R4.6_robustness_signature": r4_6_robustness(),
    }
    dest = os.path.join(str(PKG / "data/processed"), "revision_numbers.json")
    with open(dest, "w") as f:
        json.dump(result, f, indent=1)
    print(json.dumps(result, indent=1))
    print("wrote", dest)
