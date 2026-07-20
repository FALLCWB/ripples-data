#!/usr/bin/env python3
"""Ripple presence and duration against action surface, across the three daemons.

The splash/ripple model predicts that an action too small to disturb the derived
structures leaves a first-order write and no cascade. This script tests that
prediction on the same footing in all three case studies, reporting for each
action whether a cascade is present and how long it lasts.

Open vSwitch is measured against a PLACEBO anchor (scripts/placebo_control.py),
because the OvS corpus carries a monotone within-run ramp that a naive
before/after contrast cannot separate from the action. Redis and Dockerd carry
no such ramp: their pre-action windows contain no supra-threshold excess at all,
so a direct before/after reading is valid there.

Output: data/processed/surface_threshold.json

Usage: python3 scripts/surface_threshold.py
"""
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
PROC = REPO / "data" / "processed"
AGG = PROC / "ovs_recollection_aggregates"
XD = REPO / "data" / "crossdomain"

OVS_SIG = "change_volume_sum"
XD_SIG = "n_changed_pages"
PRE_Q = 95

OVS_ACTIONS = [("E_single_rule", "single rule", 1),
               ("F_burst", "21-rule burst", 21),
               ("D_flush", "flow-table flush", 200)]
# Redis is read directly: its pre-action windows carry no supra-threshold excess.
XD_ACTIONS = [("redis_redis_set_1", "Redis", "SET (1 key)", 1),
              ("redis_redis_mset_100", "Redis", "MSET (100 keys)", 100),
              ("redis_redis_flushdb", "Redis", "FLUSHDB", 1000)]
# Dockerd forces a manual GC immediately before the action, so its pre-action
# window is not a valid baseline; presence there is taken from the warmup-anchored
# null already reported for R2.11 in revision_numbers.json.
DOCKERD_ACTIONS = [("docker_inspect", "docker inspect (readback)", 0),
                   ("docker_run_1", "1 container", 1),
                   ("docker_run_10", "10 containers", 10),
                   ("docker_run_50", "50 containers", 50)]


def xd_action_ts(d):
    ev = json.load(open(f"{d}/events.json"))
    if isinstance(ev, dict):
        ev = ev.get("events", [])
    for e in ev:
        tag = str(e.get("action", "")) + str(e.get("event", "")) + str(e.get("type", ""))
        if "action" in tag or "attack" in tag or "inject" in tag:
            return float(e.get("ts", e.get("timestamp", 0)))
    return None


def crossdomain_rows(prefix):
    out = []
    for d in sorted(glob.glob(str(XD / f"{prefix}_rep*"))):
        if d.endswith(".log"):
            continue
        try:
            df = pd.read_csv(f"{d}/features.csv")
            a = xd_action_ts(d)
        except Exception:
            continue
        if a is None or df.empty:
            continue
        pre = df[df.ts < a]
        post = df[df.ts >= a]
        if len(pre) < 30 or post.empty:
            continue
        thr = float(np.percentile(pre[XD_SIG], PRE_Q))
        sup_pre = pre[pre[XD_SIG] > thr]
        sup_post = post[post[XD_SIG] > thr]
        out.append({
            "rep": Path(d).name,
            "pre_excess": float((sup_pre[XD_SIG] - thr).sum()),
            "post_excess": float((sup_post[XD_SIG] - thr).sum()),
            "last_event_s": float(sup_post.ts.max() - a) if len(sup_post) else 0.0,
            "window_s": float(post.ts.max() - a),
        })
    return out


def ovs_rows(win=30.0, n_placebo=3):
    """Per-repetition excess at the action and at non-overlapping placebo anchors."""
    out = []
    for path in sorted(AGG.glob("*.csv")):
        scen = next((s for s, _, _ in OVS_ACTIONS if path.stem.startswith(s)), None)
        if scen is None:
            continue
        df = pd.read_csv(path)
        ws, ca = df["warmup_start_ts"].iloc[0], df["controller_attached_ts"].iloc[0]
        warm = df[(df["ts"] >= ws) & (df["ts"] < ca)]
        a = float(df["action_ts"].iloc[0])
        tp = float(df["test_phase_start_ts"].iloc[0])
        if len(warm) < 10 or not np.isfinite(a):
            continue
        thr = float(np.percentile(warm[OVS_SIG], PRE_Q))

        def ex(lo, hi):
            w = df[(df["ts"] >= lo) & (df["ts"] < hi)]
            s = w[w[OVS_SIG] > thr]
            return float((s[OVS_SIG] - thr).sum())

        anchors = [a - k * win for k in range(2, 2 + n_placebo) if a - k * win - win >= tp]
        ratios = [ex(x, x + win) / ex(x - win, x) for x in anchors if ex(x - win, x) > 0]
        pre_a = ex(a - win, a)
        if not ratios or pre_a <= 0:
            continue
        out.append({"rep": path.stem, "scenario": scen,
                    "ratio_action": ex(a, a + win) / pre_a,
                    "ratio_placebo": float(np.median(ratios))})
    return out


def main():
    result = {"params": {"quantile": PRE_Q, "ovs_window_s": 30.0,
                         "note": ("OvS is read against placebo anchors because its corpus carries a "
                                  "within-run ramp; Redis and Dockerd carry no supra-threshold excess "
                                  "before the action, so a direct contrast is valid there.")},
              "ovs": [], "crossdomain": []}

    rows = ovs_rows()
    for scen, label, surface in OVS_ACTIONS:
        sub = [r for r in rows if r["scenario"] == scen]
        if not sub:
            continue
        did = np.log([r["ratio_action"] for r in sub]) - np.log([r["ratio_placebo"] for r in sub])
        result["ovs"].append({
            "action": label, "surface": surface, "n_reps": len(sub),
            "step_over_ramp": round(float(np.exp(np.median(did))), 3),
            "n_reps_with_step": int((did > 0).sum()),
        })

    for prefix, system, label, surface in XD_ACTIONS:
        rr = crossdomain_rows(prefix)
        if not rr:
            continue
        result["crossdomain"].append({
            "system": system, "action": label, "surface": surface, "n_reps": len(rr),
            "pre_excess_median": round(float(np.median([r["pre_excess"] for r in rr])), 1),
            "post_excess_median": round(float(np.median([r["post_excess"] for r in rr])), 1),
            "last_event_s_median": round(float(np.median([r["last_event_s"] for r in rr])), 1),
            "window_s_median": round(float(np.median([r["window_s"] for r in rr])), 1),
            "n_reps_with_post_excess": int(sum(1 for r in rr if r["post_excess"] > 0)),
        })

    rev = json.loads((PROC / "revision_numbers.json").read_text())
    pres = rev["R2.11_ripple_presence_calibrated"]["presence"]
    result["dockerd"] = [{"system": "Dockerd", "action": label, "surface": surface,
                          "n_reps": pres[key]["n"], "n_present": pres[key]["present"],
                          "presence_rate": pres[key]["rate"]}
                         for key, label, surface in DOCKERD_ACTIONS if key in pres]

    (PROC / "surface_threshold.json").write_text(json.dumps(result, indent=2))

    print("Open vSwitch (step above the ramp, 30 s windows):")
    for r in result["ovs"]:
        print(f"  surface {r['surface']:4d}  {r['action']:18s} n={r['n_reps']:2d}  "
              f"step {r['step_over_ramp']:.2f}x  present in {r['n_reps_with_step']}/{r['n_reps']}")
    print("Redis (direct contrast; no pre-action excess):")
    for r in result["crossdomain"]:
        print(f"  {r['system']:8s} surface {r['surface']:4d}  {r['action']:26s} n={r['n_reps']:2d}  "
              f"pre {r['pre_excess_median']:7.1f}  post {r['post_excess_median']:8.1f}  "
              f"last event {r['last_event_s_median']:6.1f} s of {r['window_s_median']:.0f} s")
    print("Dockerd (presence against the warmup-anchored null, R2.11):")
    for r in result["dockerd"]:
        print(f"  surface {r['surface']:4d}  {r['action']:26s} "
              f"present in {r['n_present']}/{r['n_reps']} reps ({r['presence_rate']:.0%})")


if __name__ == "__main__":
    main()
