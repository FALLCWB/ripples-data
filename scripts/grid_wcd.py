#!/usr/bin/env python3
"""
grid_wcd — W x C x Delta sensitivity grid (frozen protocol P5.4, reviewers R2#6/R4#2).

Descriptive analysis only (no per-cell p-values): for every grid cell reports
per-scenario label counts, % of events whose label CHANGES vs the default cell
(W=2.0, C=5.0, Delta=300), induced-cascade recall over ground-truth induced
actions, and the spurious direct-anchor rate under circularly shifted audit
timestamps (negative control, P5.5b).

Each rep is parsed ONCE (features CSV -> event timestamps; audit; actions);
classification is then re-run per cell, which is cheap.

Usage:
  python3 grid_wcd.py --snapshots DIR [--order corrected] [--audit-mode rich]
                      [--out grid.json] [--shift-control]
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

import labeler_v2 as L
import rep_io

W_GRID = [0.5, 1.0, 2.0, 4.0, 8.0]
C_GRID = [2.5, 5.0, 10.0]
D_GRID = [60.0, 150.0, 300.0, 600.0]
DEFAULT = (2.0, 5.0, 300.0)
SHIFT_S = 97.0  # circular shift for the negative control (prime-ish, >> W_max)


def load_rep_once(sd: Path, audit_mode: str):
    rep = rep_io.load_rep(sd)                      # shared reader
    if rep is None or rep.excluded:
        return None
    t_s, t_e, thr = rep.t_s, rep.t_e, rep.threshold
    ev_ts = rep.test[rep.test["signal"] > thr]["ts"].values.astype(float)
    by_cat = dict(rep.by_cat or {})
    if audit_mode == "sparse":
        by_cat = {c: v for c, v in by_cat.items() if c not in L.SPARSE_DROP}
        audit = np.array(sorted(set(t for arr in by_cat.values() for t in arr)))
    else:
        audit = rep.audit if rep.audit is not None else np.array([])
    induced, legit = rep.induced, rep.legit
    lldp_a = by_cat.get("LLDP_REFRESH", by_cat.get("LINK_EVENT", np.array([])))
    stats_a = by_cat.get("STATS_RESPONSE", by_cat.get("PORT_EVENT", np.array([])))
    # audit restricted to the test phase, for the shift negative control:
    # shifting warmup-phase entries would map them INTO the test window and
    # invent anchors that never existed there (bug flagged in review).
    audit_test = audit[audit >= t_s] if len(audit) else audit
    return {
        "name": sd.name, "scenario": sd.name.split("_rep")[0],
        "ev_ts": ev_ts, "audit": audit, "audit_test": audit_test,
        "lldp_ticks": L.predict_cadence(lldp_a, L.LLDP_PERIOD, t_s, t_e),
        "stats_ticks": L.predict_cadence(stats_a, L.STATS_PERIOD, t_s, t_e),
        "induced": induced, "legit": legit,
        "t_s": t_s, "t_e": t_e, "dur_h": (t_e - t_s) / 3600.0,
    }


def labels_for(rep, order, W, C, D, audit=None):
    audit = rep["audit"] if audit is None else audit
    return [L.classify(float(ts), order, audit, rep["lldp_ticks"],
                       rep["stats_ticks"], rep["induced"], rep["legit"], W, C, D)
            for ts in rep["ev_ts"]]


def induced_recall(rep, labels, D_ref):
    """Fraction of ground-truth-induced events labelled Induced-cascade.

    Ground truth is defined by a FIXED reference window D_ref (the default
    300 s), NOT the swept cell's Δ. If the ground truth used the cell's Δ the
    recall would be tautologically 1.0 (an event is ground-truth-induced iff it
    falls in the labeler's window). With a fixed reference, a cell whose Δ is
    smaller than D_ref leaves late induced events outside its window, so they
    are labelled something else and recall drops — which is what R2.6 asks the
    grid to reveal (protocol P5.5a: ground truth = the actor's injection, not
    the swept window)."""
    gt = [i for i, ts in enumerate(rep["ev_ts"])
          if L.active_aftermath(float(ts), rep["induced"], D_ref) is not None]
    if not gt:
        return None
    hit = sum(1 for i in gt if labels[i] == "Induced-cascade")
    return hit / len(gt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", type=Path, required=True)
    ap.add_argument("--order", default="corrected",
                    choices=["corrected", "submitted", "naive"])
    ap.add_argument("--audit-mode", default="rich", choices=["rich", "sparse"])
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--shift-control", action="store_true",
                    help="also compute spurious direct-anchor rate under "
                         "circularly shifted audit (negative control)")
    args = ap.parse_args()

    reps = []
    for sd in sorted(args.snapshots.iterdir()):
        if not (sd.is_dir() and (sd / "markers.json").exists()):
            continue
        r = load_rep_once(sd, args.audit_mode)
        if r is None:
            print(f"{sd.name}: excluded")
            continue
        reps.append(r)
        print(f"loaded {r['name']}: {len(r['ev_ts'])} events, "
              f"{len(r['audit'])} audit, {len(r['induced'])} induced")

    base = {r["name"]: labels_for(r, args.order, *DEFAULT) for r in reps}
    cells = []
    for W in W_GRID:
        for C in C_GRID:
            for D in D_GRID:
                per_scenario = defaultdict(lambda: defaultdict(int))
                changed = total = 0
                recalls = []
                spurious = []
                for r in reps:
                    labs = labels_for(r, args.order, W, C, D)
                    for lab in labs:
                        per_scenario[r["scenario"]][lab] += 1
                    changed += sum(1 for a, b in zip(labs, base[r["name"]]) if a != b)
                    total += len(labs)
                    rec = induced_recall(r, labs, DEFAULT[2])  # fixed reference Δ=300
                    if rec is not None:
                        recalls.append(rec)
                    if args.shift_control and len(r["audit_test"]):
                        span = r["t_e"] - r["t_s"]
                        # circular shift of TEST-phase anchors, averaged over
                        # several offsets to reduce variance of the control.
                        rates = []
                        for off in (37.0, 97.0, 151.0, 211.0):
                            shifted = np.sort(r["t_s"] +
                                              (r["audit_test"] - r["t_s"] + off) % max(span, 1.0))
                            slabs = labels_for(r, args.order, W, C, D, audit=shifted)
                            rates.append(sum(1 for x in slabs if x == "Direct-anchor")
                                         / max(len(slabs), 1))
                        spurious.append(float(np.mean(rates)))
                cells.append({
                    "W": W, "C": C, "delta": D,
                    "pct_label_changed_vs_default": round(100.0 * changed / max(total, 1), 2),
                    "induced_recall_mean": round(float(np.mean(recalls)), 4) if recalls else None,
                    "spurious_direct_rate_mean": round(float(np.mean(spurious)), 4) if spurious else None,
                    "per_scenario_counts": {s: dict(c) for s, c in per_scenario.items()},
                })
                print(f"W={W} C={C} D={D}: changed={cells[-1]['pct_label_changed_vs_default']}% "
                      f"recall={cells[-1]['induced_recall_mean']}")

    out = args.out or (args.snapshots.parent / "analysis" /
                       f"grid_{args.order}_{args.audit_mode}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"order": args.order, "audit_mode": args.audit_mode,
                               "default_cell": DEFAULT, "n_reps": len(reps),
                               "cells": cells}, indent=1))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
