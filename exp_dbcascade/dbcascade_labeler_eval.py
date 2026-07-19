#!/usr/bin/env python3
"""Controlled ground-truth test of the labeler attribution ORDER on the
DB-cascade oracle (R2.12), using the paper's exact classify().

Ground truth is assigned by PROVENANCE, from each action's own write
timestamps in the audit log, NOT from the labeler's classification windows
(W, C, Delta), so the correctness test is not circular:

  - REACTIVE window  [t0, cascade_end + 0.5 s]: the ingest's delayed cascade is
    the only active cause (the admin has not fired yet). Every supra-threshold
    event here is reactive -> true Induced-cascade. This is the discriminating,
    non-circular test of the ordering rule.
  - ADMIN window     [max(t_leg, react_end), t_leg + 1.5 s]: the independent
    operator action's own brief in-place write -> true Direct-anchor. This
    window coincides with the labeler's near-anchor exception (|t - t_leg| <= W),
    so the priority order recovering it is a consistency check, not independent.
  - AMBIENT: supra-threshold events in neither footprint (background/decay).
    Reported separately, broken down by label per order.

The pure ordering ablation is corrected (Induced-before-Direct) vs submitted
(Direct-before-Induced): SAME rules, only the precedence differs. Reps whose
ingest failed are dropped (with an explicit audit trail).

The outcome is deterministic across reps (per-rep recall has zero variance), so
recall is reported as reps-count and pooled-event fractions, NOT as a bootstrap
CI, which would be tautological on a zero-variance vector.
"""
import csv
import glob
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))   # vendored labeler_v2.py + rep_io.py
from labeler_v2 import classify  # noqa: E402

W, C, DELTA = 2.0, 5.0, 300.0
REACT_SETTLE, ADMIN_SETTLE = 0.5, 1.5
ACTIVE = ("Induced-cascade", "Direct-anchor")


def eval_rep(rep):
    rep = Path(rep)
    name = rep.name
    if not (rep / "markers.json").exists():           # incomplete rep (still collecting)
        return {"drop": f"{name}: no markers.json"}
    m = json.loads((rep / "markers.json").read_text())
    if not m.get("ingest_ok", False):
        return {"drop": f"{name}: ingest_ok=false"}
    t0, t_leg = m["action_ts"], m["legit_ts"]
    rows = list(csv.DictReader(open(rep / "features.csv")))
    ts = np.array([float(r["ts"]) for r in rows])
    sig = np.array([float(r["change_vol_bytes"]) for r in rows])
    pre = sig[ts < t0]
    thr = np.percentile(pre, 95) if len(pre) else 0.0
    if thr <= 0:                                       # need a real warmup baseline; else supra() over-flags
        return {"drop": f"{name}: no usable warmup threshold (thr={thr})"}

    a_rows = [json.loads(l) for l in open(rep / "audit.jsonl")]
    audit = np.array(sorted(x["ts"] for x in a_rows))
    induced = [x["ts"] for x in a_rows if x["role"] == "induced"]
    legit = [x["ts"] for x in a_rows if x["role"] == "legit"]
    reactive_ts = [x["ts"] for x in a_rows if x["role"] == "reactive"]
    if not reactive_ts:                                # guard: empty max() would abort the whole run
        return {"drop": f"{name}: no reactive audit rows"}
    cascade_end = max(reactive_ts)

    r_hi = cascade_end + REACT_SETTLE                  # end of the reactive window
    a_lo = max(t_leg, r_hi)                            # admin window forced disjoint from the reactive one
    a_hi = t_leg + ADMIN_SETTLE

    def supra(lo, hi):
        return [t for t, s in zip(ts, sig) if s > thr and lo <= t <= hi]
    react = supra(t0, r_hi)
    admin = supra(a_lo, a_hi)
    in_r = lambda t: t0 <= t <= r_hi
    in_a = lambda t: a_lo <= t <= a_hi
    ambient = [t for t, s in zip(ts, sig) if s > thr and t >= t0 and not in_r(t) and not in_a(t)]

    def cl(t, order):
        return classify(t, order, audit, [], [], induced, legit, W, C, DELTA)
    out = {"n_react": len(react), "n_admin": len(admin), "n_ambient": len(ambient)}
    # per-rep reactive recall (deterministic across reps; tracked to report reps-full/reps-zero)
    out["corr_react_full"] = bool(react) and all(cl(t, "corrected") == "Induced-cascade" for t in react)
    out["sub_react_none"] = bool(react) and all(cl(t, "submitted") != "Induced-cascade" for t in react)
    out["corr_react_hits"] = sum(cl(t, "corrected") == "Induced-cascade" for t in react)
    out["sub_react_hits"] = sum(cl(t, "submitted") == "Induced-cascade" for t in react)
    out["corr_admin_hits"] = sum(cl(t, "corrected") == "Direct-anchor" for t in admin)
    # ambient label breakdown per order + per-event disagreement
    out["amb_corr"] = Counter(cl(t, "corrected") for t in ambient)
    out["amb_sub"] = Counter(cl(t, "submitted") for t in ambient)
    out["amb_disagree"] = sum(cl(t, "corrected") != cl(t, "submitted") for t in ambient)
    return out


reps = sorted(set(glob.glob(str(Path(sys.argv[1]) / "*/rep*")) + glob.glob(str(Path(sys.argv[1]) / "rep*"))))
R, drops = [], []
for r in reps:
    o = eval_rep(r)
    (drops if "drop" in o else R).append(o["drop"] if "drop" in o else o)
for d in drops:                                        # explicit exclusion audit trail
    print(f"[dropped] {d}", file=sys.stderr)


def s(key):
    return sum(x[key] for x in R)


def amb_counts(key):
    c = Counter()
    for x in R:
        c.update(x[key])
    return {k: c[k] for k in ACTIVE if c[k]} or {k: 0 for k in ACTIVE}


Nr, Na, Namb = s("n_react"), s("n_admin"), s("n_ambient")
result = {
    "n_reps": len(R), "n_dropped": len(drops), "dropped": drops,
    "ground_truth": "provenance windows from each action's own audit writes (roles), "
                    "independent of the labeler's W/C/Delta windows",
    "deterministic": True,
    "note": "Per-rep recall has zero variance across reps, so recall is reported as "
            "reps-count and pooled-event fractions, not a bootstrap CI.",
    "W": W, "C": C, "DELTA": DELTA,
    "reactive_window": {
        "n_events": Nr,
        "corrected_induced_first": {
            "reps_full_recall": s("corr_react_full"), "reps_total": len(R),
            "events_recovered": s("corr_react_hits"), "events_total": Nr,
            "recall": round(s("corr_react_hits") / Nr, 3) if Nr else None},
        "submitted_direct_first": {
            "reps_zero_recall": s("sub_react_none"), "reps_total": len(R),
            "events_recovered": s("sub_react_hits"), "events_total": Nr,
            "recall": round(s("sub_react_hits") / Nr, 3) if Nr else None},
    },
    "admin_window": {"n_events": Na, "corrected_to_direct": s("corr_admin_hits"),
                     "recall": round(s("corr_admin_hits") / Na, 3) if Na else None},
    "ambient": {"n_events": Namb, "corrected": amb_counts("amb_corr"),
                "submitted": amb_counts("amb_sub"), "orders_disagree": s("amb_disagree")},
}
print(json.dumps(result, indent=1))
out_path = Path(__file__).resolve().parent / "dbcascade_result.json"   # release-stable path
out_path.write_text(json.dumps(result, indent=1))
print(f"\nwrote {out_path}", file=sys.stderr)
