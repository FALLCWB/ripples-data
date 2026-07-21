#!/usr/bin/env python3
"""
labeler_v2 — deterministic six-category labeler, pre-registered order.

Implements P2 of the frozen protocol (ripples-paper/notes/PROTOCOLO-PRE-REGISTRADO.md,
freeze commit 67cb280): Induced-cascade is tested BEFORE Direct-anchor inside the
aftermath window, with an exception for independent scripted legitimate actions.
This fixes the priority inversion flagged by reviewer R2#1 (the submitted paper's
pipeline tested Direct-anchor first: regen_figs_data.py::classify).

Also implements the two comparison variants for the order ablation (P5.6):
  corrected : Induced -> Direct -> Reactive -> Periodic -> Endogenous -> Indeterminate
  submitted : Direct -> Induced -> Reactive -> Periodic -> Endogenous -> Indeterminate
  naive     : Direct -> Reactive-as-direct fallback only (no induced window at all)

Supports MULTIPLE induced actions per rep (scenarios G/H, overlapping aftermaths):
the overlap rule assigns an event to the MOST RECENT action with t_a <= ts (P2).

Event flagging (P1, unchanged): per-iteration change_volume_sum > per-rep
95th percentile of the warmup signal.

Sparse-audit derived view (protocol addendum 2026-07-14, answers R3#3): the
sparse audit era is the rich provenance with the categories added by memsdn
commit 85eb250 removed. Recording those categories is passive (ONOS generates
the events regardless), so sparse is a strict subset of rich by construction
and can be derived post-hoc for EVERY scenario:
    --audit-mode sparse  drops {LLDP_REFRESH, STATS_RESPONSE, DEVICE_UPDATE,
                                PORT_UPDATE}
    --audit-mode rich    keeps everything (default)

Usage:
  python3 labeler_v2.py --snapshots ~/research/ripples-recollection/snapshots \
      [--order corrected|submitted|naive] [--audit-mode rich|sparse] \
      [--W 2.0] [--C 5.0] [--delta 300] [--out results.json]

Output: one JSON with per-rep category counts, per-hour rates, threshold,
duration, n iterations, clock offset (from events.json), and the parameter set —
the input to the scenario decomposition (Table 2 of the paper) and to the sensitivity grid (P5.4 runs this over the grid).
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

import rep_io

SPARSE_DROP = {"LLDP_REFRESH", "STATS_RESPONSE", "DEVICE_UPDATE", "PORT_UPDATE"}

LLDP_PERIOD = 3.0
STATS_PERIOD = 5.0
CADENCE_TOL = 1.0
W_CTX = 2.0
PRE_QUANTILE = rep_io.PRE_QUANTILE  # single source

CATEGORIES = ["Direct-anchor", "Induced-cascade", "Reactive-cascade",
              "Periodic-gap", "Endogenous-unexplained", "Indeterminate"]


def dist_nearest(ts: float, arr: np.ndarray) -> float:
    if len(arr) == 0:
        return float("inf")
    idx = int(np.searchsorted(arr, ts))
    d = float("inf")
    if idx > 0:
        d = min(d, ts - arr[idx - 1])
    if idx < len(arr):
        d = min(d, arr[idx] - ts)
    return d


def predict_cadence(anchor: np.ndarray, period: float, t_s: float, t_e: float) -> np.ndarray:
    if len(anchor) == 0:
        return np.array([])
    origin = anchor[0]
    lo = -int((origin - t_s) / period) - 1
    hi = int((t_e - origin) / period) + 2
    ticks = [origin + i * period for i in range(lo, hi)]
    return np.array([t for t in ticks if t_s - 2 <= t <= t_e + 2])


def context(ts, audit, lldp_ticks, stats_ticks):
    d_a = dist_nearest(ts, audit)
    d_c = min(dist_nearest(ts, lldp_ticks), dist_nearest(ts, stats_ticks))
    if d_a <= W_CTX or d_c <= CADENCE_TOL:
        return "Controller"
    if d_a > W_CTX and d_c > CADENCE_TOL:
        return "Switch"
    return "ND"


def active_aftermath(ts: float, induced: list[float], delta: float):
    """Most recent induced action with t_a <= ts <= t_a + delta (P2 overlap rule)."""
    best = None
    for t_a in induced:
        if t_a <= ts <= t_a + delta and (best is None or t_a > best):
            best = t_a
    return best


def classify(ts, order, audit, lldp_ticks, stats_ticks,
             induced, legit, W, C, delta):
    d_a = dist_nearest(ts, audit)
    in_aftermath = active_aftermath(ts, induced, delta) is not None
    near_legit = any(abs(ts - t_l) <= W for t_l in legit)

    def induced_rule(with_exception=True):
        # Exception (P2 step 1): an independent scripted legitimate action within
        # +-W keeps its Direct-anchor attribution even inside the aftermath. The
        # literal-May binary did NOT have this exception; with_exception=False
        # reproduces it so the ablation can separate ordering from the exception.
        return in_aftermath and (not near_legit if with_exception else True)

    def direct_rule():
        return d_a <= W

    def reactive_rule():
        if len(audit) == 0:
            return False
        idx = int(np.searchsorted(audit, ts - C))
        return idx < len(audit) and audit[idx] < ts - W

    if order == "corrected":                 # frozen P2: Induced before Direct, w/ exception
        if induced_rule():
            return "Induced-cascade"
        if direct_rule():
            return "Direct-anchor"
    elif order == "submitted":               # Direct before Induced, w/ exception (isolates ordering)
        if direct_rule():
            return "Direct-anchor"
        if induced_rule():
            return "Induced-cascade"
    elif order == "literal_may":             # Direct before Induced, NO exception (the shipped binary)
        if direct_rule():
            return "Direct-anchor"
        if induced_rule(with_exception=False):
            return "Induced-cascade"
    elif order == "naive":                    # temporal anchor only, no induced window
        if direct_rule():
            return "Direct-anchor"
    else:
        raise ValueError(order)

    if reactive_rule():
        return "Reactive-cascade"
    ctx = context(ts, audit, lldp_ticks, stats_ticks)
    if ctx == "Controller":
        return "Periodic-gap"
    if ctx == "Switch":
        return "Endogenous-unexplained"
    return "Indeterminate"


def analyze_rep(sd: Path, order: str, audit_mode: str, W: float, C: float, delta: float):
    rep = rep_io.load_rep(sd)                      # single shared reader (signal, threshold, audit)
    if rep is None:
        return None
    if rep.excluded:
        return {"rep": rep.name, "excluded": True, "reason": rep.reason}
    pre, test, thr, t_s, t_e = rep.pre, rep.test, rep.threshold, rep.t_s, rep.t_e
    by_cat = dict(rep.by_cat or {})
    if audit_mode == "sparse":
        by_cat = {c: v for c, v in by_cat.items() if c not in SPARSE_DROP}
        audit = np.array(sorted(set(t for arr in by_cat.values() for t in arr)))
    else:
        audit = rep.audit if rep.audit is not None else np.array([])
    induced, legit = rep.induced, rep.legit
    ev = test[test["signal"] > thr]
    lldp_a = by_cat.get("LLDP_REFRESH", by_cat.get("LINK_EVENT", np.array([])))
    stats_a = by_cat.get("STATS_RESPONSE", by_cat.get("PORT_EVENT", np.array([])))
    lldp_ticks = predict_cadence(lldp_a, LLDP_PERIOD, t_s, t_e)
    stats_ticks = predict_cadence(stats_a, STATS_PERIOD, t_s, t_e)

    counts = defaultdict(int)
    labels = []
    for ts in ev["ts"].values:
        lab = classify(float(ts), order, audit, lldp_ticks, stats_ticks,
                       induced, legit, W, C, delta)
        counts[lab] += 1
        labels.append({"ts": float(ts), "label": lab})
    dur_h = (t_e - t_s) / 3600.0
    return {
        "rep": sd.name,
        "scenario": sd.name.split("_rep")[0],
        "excluded": False,
        "order": order, "audit_mode": audit_mode,
        "W": W, "C": C, "delta": delta,
        "threshold": thr,
        "n_iters_pre": int(len(pre)), "n_iters_test": int(len(test)),
        "n_events": int(len(ev)),
        "n_induced_actions": len(induced), "n_legit_actions": len(legit),
        "n_audit_entries": int(len(audit)),
        "dur_h": dur_h,
        "counts": {c: int(counts.get(c, 0)) for c in CATEGORIES},
        "rates_per_h": {c: round(counts.get(c, 0) / max(dur_h, 1e-9), 1)
                        for c in CATEGORIES},
        "labels": labels,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", type=Path, required=True)
    ap.add_argument("--order", default="corrected",
                    choices=["corrected", "submitted", "naive"])
    ap.add_argument("--audit-mode", default="rich", choices=["rich", "sparse"])
    ap.add_argument("--W", type=float, default=2.0)
    ap.add_argument("--C", type=float, default=5.0)
    ap.add_argument("--delta", type=float, default=300.0)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    dirs = sorted(d for d in args.snapshots.iterdir()
                  if d.is_dir() and (d / "markers.json").exists())
    results = []
    for sd in dirs:
        r = analyze_rep(sd, args.order, args.audit_mode, args.W, args.C, args.delta)
        if r is None:
            continue
        results.append(r)
        if r.get("excluded"):
            print(f"{sd.name}: EXCLUDED ({r['reason']})")
        else:
            top = {k: v for k, v in r["counts"].items() if v}
            print(f"{sd.name}: {r['n_events']} events -> {top}")
    out = args.out or args.snapshots / (
        f"labels_{args.order}_{args.audit_mode}_W{args.W}_C{args.C}_D{int(args.delta)}.json")
    out.write_text(json.dumps({"params": vars(args) | {"snapshots": str(args.snapshots),
                                                       "out": str(out)},
                               "results": results}, indent=1, default=str))
    print(f"\nwrote {out} ({len(results)} reps)")


if __name__ == "__main__":
    main()
