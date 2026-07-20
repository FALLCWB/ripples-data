#!/usr/bin/env python3
"""Placebo-anchored control for the post-action excess and persistence readings.

The induced action is delivered at a nearly fixed elapsed offset in every
repetition, and the excess signal ramps monotonically through a run. "After the
action" is therefore confounded with "later in the run", and a before/after
contrast at the action alone cannot separate the two.

This script measures the confound directly and then removes it. A PLACEBO anchor
is placed OFFSET seconds before the real action, with both of its windows inside
the live, controller-attached pre-action phase, where no action is delivered. The
same statistic is computed at both anchors, and the action's effect is the
difference between them, paired within repetition:

    delta = log(post/pre at the action) - log(post/pre at the placebo)

A ramp that is common to both anchors cancels in delta; what remains is the step
the action adds on top of the ramp. The same construction is applied to the
persistence criterion (fraction of aftermath bins whose excess exceeds the 95th
percentile of the anchor's own preceding bins).

Output: data/processed/placebo_control.json

Usage: python3 scripts/placebo_control.py [--win 120] [--offset 120] [--bin 10]
"""
import argparse
import json
import statistics as st
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
AGG = PROC / "ovs_recollection_aggregates"
SIG = "change_volume_sum"
PRE_Q = 95
REF_Q = 95
INDUCED = ("D_flush", "E_single_rule", "F_burst")


def load(path: Path):
    df = pd.read_csv(path)
    ws, ca = df["warmup_start_ts"].iloc[0], df["controller_attached_ts"].iloc[0]
    warm = df[(df["ts"] >= ws) & (df["ts"] < ca)]
    action_ts = float(df["action_ts"].iloc[0])
    if len(warm) < 10 or not np.isfinite(action_ts):
        return None
    return df, float(np.percentile(warm[SIG], PRE_Q)), action_ts


def excess(df, thr, lo, hi):
    w = df[(df["ts"] >= lo) & (df["ts"] < hi)]
    s = w[w[SIG] > thr]
    return float((s[SIG] - thr).sum())


def bins(df, thr, lo, hi, bin_s):
    out, start = [], lo
    while start + bin_s <= hi:
        w = df[(df["ts"] >= start) & (df["ts"] < start + bin_s)]
        s = w[w[SIG] > thr] if len(w) else w
        out.append(float((s[SIG] - thr).sum()) if len(w) else 0.0)
        start += bin_s
    return out


def active_fraction(df, thr, anchor, pre_s, post_s, bin_s):
    ref = bins(df, thr, anchor - pre_s, anchor, bin_s)
    post = bins(df, thr, anchor, anchor + post_s, bin_s)
    if len(ref) < 5 or not post:
        return None
    rt = float(np.percentile(ref, REF_Q))
    return sum(1 for b in post if b > rt) / len(post)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--win", type=float, default=60.0, help="window length each side of an anchor")
    ap.add_argument("--offset", type=float, default=180.0, help="placebo anchor, seconds before the action")
    ap.add_argument("--bin", type=float, default=10.0)
    ap.add_argument("--n-placebo", type=int, default=3,
                    help="number of non-overlapping placebo anchors used to estimate the ramp")
    ap.add_argument("--persist-pre", type=float, default=120.0)
    ap.add_argument("--persist-post", type=float, default=300.0)
    args = ap.parse_args()

    rows = []
    for path in sorted(AGG.glob("*.csv")):
        scen = next((s for s in INDUCED if path.stem.startswith(s)), None)
        if scen is None:
            continue
        loaded = load(path)
        if loaded is None:
            continue
        df, thr, a = loaded
        tp = float(df["test_phase_start_ts"].iloc[0])
        # Several placebo anchors, all non-overlapping and inside the live phase:
        # the ramp is not linear, so a single anchor estimates it noisily.
        anchors, k = [], 2
        while a - k * args.win - args.win >= tp and len(anchors) < args.n_placebo:
            anchors.append(a - k * args.win)
            k += 1
        if not anchors:
            continue
        ratios = []
        for anc in anchors:
            pre_p = excess(df, thr, anc - args.win, anc)
            post_p = excess(df, thr, anc, anc + args.win)
            if pre_p > 0:
                ratios.append(post_p / pre_p)
        if not ratios:
            continue
        placebo = anchors[0]
        r = {"rep": path.stem, "scenario": scen,
             "n_placebo_anchors": len(ratios),
             "pre_action": excess(df, thr, a - args.win, a),
             "post_action": excess(df, thr, a, a + args.win),
             "pre_placebo": excess(df, thr, placebo - args.win, placebo),
             "post_placebo": excess(df, thr, placebo, placebo + args.win)}
        if r["pre_action"] <= 0:
            continue
        r["ratio_action"] = r["post_action"] / r["pre_action"]
        r["ratio_placebo"] = float(np.median(ratios))
        r["log_did"] = float(np.log(r["ratio_action"]) - np.log(r["ratio_placebo"]))
        fa = active_fraction(df, thr, a, args.persist_pre, args.persist_post, args.bin)
        fp = active_fraction(df, thr, placebo, args.persist_pre, args.persist_post, args.bin)
        r["active_fraction_action"] = fa
        r["active_fraction_placebo"] = fp
        rows.append(r)

    if not rows:
        raise SystemExit("no usable repetitions; try a smaller --win or --offset")

    ra = np.array([r["ratio_action"] for r in rows])
    rp = np.array([r["ratio_placebo"] for r in rows])
    did = np.array([r["log_did"] for r in rows])
    _, p_action = wilcoxon(np.array([r["post_action"] for r in rows]),
                           np.array([r["pre_action"] for r in rows]), alternative="two-sided")
    _, p_placebo = wilcoxon(np.array([r["post_placebo"] for r in rows]),
                            np.array([r["pre_placebo"] for r in rows]), alternative="two-sided")
    _, p_did = wilcoxon(did, alternative="two-sided")

    fa = np.array([r["active_fraction_action"] for r in rows if r["active_fraction_action"] is not None])
    fp = np.array([r["active_fraction_placebo"] for r in rows if r["active_fraction_placebo"] is not None])
    _, p_fr = wilcoxon(fa, fp, alternative="two-sided")

    out = {
        "params": {"window_s": args.win, "placebo_offset_s": args.offset, "bin_s": args.bin,
                   "persistence_pre_s": args.persist_pre, "persistence_post_s": args.persist_post,
                   "threshold": f"p{PRE_Q} of warmup {SIG}", "n_reps": len(rows)},
        "note": ("The placebo anchor sits inside the live pre-action phase, so any ramp common to "
                 "both anchors cancels in the difference. p_action and p_placebo are the naive "
                 "before/after tests at each anchor; the action's own effect is the difference."),
        "excess": {
            "ratio_action_median": round(float(np.median(ra)), 3),
            "ratio_placebo_median": round(float(np.median(rp)), 3),
            "p_action_naive": float(f"{p_action:.3g}"),
            "p_placebo_naive": float(f"{p_placebo:.3g}"),
            "did_log_median": round(float(np.median(did)), 4),
            "did_ratio_median": round(float(np.exp(np.median(did))), 3),
            "n_reps_did_positive": int((did > 0).sum()),
            "p_did": float(f"{p_did:.3g}"),
        },
        "persistence": {
            "active_fraction_action_median": round(float(np.median(fa)), 3),
            "active_fraction_placebo_median": round(float(np.median(fp)), 3),
            "n_reps_action_greater": int((fa > fp).sum()),
            "n_reps": int(len(fa)),
            "p_paired": float(f"{p_fr:.3g}"),
        },
        "per_scenario": {},
        "per_rep": rows,
    }
    for scen in INDUCED:
        sub = [r for r in rows if r["scenario"] == scen]
        if not sub:
            continue
        d = np.array([r["log_did"] for r in sub])
        out["per_scenario"][scen] = {
            "n_reps": len(sub),
            "ratio_action_median": round(float(np.median([r["ratio_action"] for r in sub])), 3),
            "ratio_placebo_median": round(float(np.median([r["ratio_placebo"] for r in sub])), 3),
            "did_ratio_median": round(float(np.exp(np.median(d))), 3),
            "n_reps_did_positive": int((d > 0).sum()),
            "active_fraction_action_median": round(float(st.median(
                [r["active_fraction_action"] for r in sub if r["active_fraction_action"] is not None])), 3),
            "active_fraction_placebo_median": round(float(st.median(
                [r["active_fraction_placebo"] for r in sub if r["active_fraction_placebo"] is not None])), 3),
        }

    (PROC / "placebo_control.json").write_text(json.dumps(out, indent=2))
    e, q = out["excess"], out["persistence"]
    print(f"n = {len(rows)} reps, window {args.win:.0f} s, placebo {args.offset:.0f} s before the action")
    print(f"  excess ratio at the action  : {e['ratio_action_median']}  (naive p = {e['p_action_naive']})")
    print(f"  excess ratio at the placebo : {e['ratio_placebo_median']}  (naive p = {e['p_placebo_naive']})")
    print(f"  difference in differences   : {e['did_ratio_median']}x  positive in "
          f"{e['n_reps_did_positive']}/{len(rows)}  p = {e['p_did']}")
    print(f"  active-bin fraction         : {q['active_fraction_action_median']} at the action vs "
          f"{q['active_fraction_placebo_median']} at the placebo, higher in "
          f"{q['n_reps_action_greater']}/{q['n_reps']}, p = {q['p_paired']}")
    for scen, r in out["per_scenario"].items():
        print(f"    {scen:16s} DiD {r['did_ratio_median']:.2f}x (+{r['n_reps_did_positive']}/{r['n_reps']})  "
              f"bins {r['active_fraction_action_median']:.2f} vs {r['active_fraction_placebo_median']:.2f}")


if __name__ == "__main__":
    main()
