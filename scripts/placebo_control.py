#!/usr/bin/env python3
"""Placebo-anchored control for the post-action excess reading.

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
the action adds on top of the ramp. Duration is not estimated here: no same-run comparator admits an
action-free window of the length a persistence criterion needs. Lag-resolved
duration is computed by scripts/lag_profile.py instead.

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
# Scenarios with no induced action: a sham anchor is placed at the elapsed offset
# where the induced scenarios deliver theirs, so the machinery runs where there is
# nothing to find.
SHAM = ("A_idle", "B_flow_install", "C_ping_sustained")
SHAM_OFFSET_S = 288.0


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--win", type=float, default=60.0, help="window length each side of an anchor")

    ap.add_argument("--bin", type=float, default=10.0)
    ap.add_argument("--n-placebo", type=int, default=3,
                    help="number of non-overlapping placebo anchors used to estimate the ramp")
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
            k += 2
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


    out = {
        "params": {"window_s": args.win, "bin_s": args.bin,
                   "placebo_anchors_at": [f"action - {k} x window" for k in range(2, 2 + args.n_placebo)],
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
        }

    # Negative control: same statistic on scenarios with no induced action.
    sham = {}
    for scen in SHAM:
        vals = []
        for path in sorted(AGG.glob(f"{scen}_rep*.csv")):
            df = pd.read_csv(path)
            ws, ca = df["warmup_start_ts"].iloc[0], df["controller_attached_ts"].iloc[0]
            warm = df[(df["ts"] >= ws) & (df["ts"] < ca)]
            tp = float(df["test_phase_start_ts"].iloc[0])
            if len(warm) < 10:
                continue
            thr = float(np.percentile(warm[SIG], PRE_Q))
            anc = tp + SHAM_OFFSET_S
            pre_s = excess(df, thr, anc - args.win, anc)
            ratios = [excess(df, thr, x, x + args.win) / excess(df, thr, x - args.win, x)
                      for x in (anc - k * args.win for k in range(2, 2 + args.n_placebo))
                      if x - args.win >= tp and excess(df, thr, x - args.win, x) > 0]
            if not ratios or pre_s <= 0:
                continue
            vals.append(float(np.log(excess(df, thr, anc, anc + args.win) / pre_s)
                              - np.log(float(np.median(ratios)))))
        if vals:
            v = np.array(vals)
            sham[scen] = {"n_reps": len(v),
                          "did_ratio_median": round(float(np.exp(np.median(v))), 3),
                          "n_reps_positive": int((v > 0).sum())}
    out["sham_anchor_no_action_scenarios"] = {
        "offset_s": SHAM_OFFSET_S,
        "note": ("Same difference in differences on scenarios that contain no induced "
                 "action, sham anchor at the elapsed offset where the induced scenarios "
                 "deliver theirs."),
        "per_scenario": sham}

    # Window sweep, so the decay reported in the paper is in the released output.
    sweep = []
    for w in (20.0, 30.0, 45.0, 60.0):
        if w == args.win:
            sweep.append({"window_s": w, "did_ratio_median": out["excess"]["did_ratio_median"],
                          "n_reps_did_positive": out["excess"]["n_reps_did_positive"],
                          "p_did": out["excess"]["p_did"]})
            continue
        rr = []
        for path in sorted(AGG.glob("*.csv")):
            scen = next((x for x in INDUCED if path.stem.startswith(x)), None)
            if scen is None:
                continue
            df = pd.read_csv(path)
            ws, ca = df["warmup_start_ts"].iloc[0], df["controller_attached_ts"].iloc[0]
            warm = df[(df["ts"] >= ws) & (df["ts"] < ca)]
            a_ts = float(df["action_ts"].iloc[0])
            tp = float(df["test_phase_start_ts"].iloc[0])
            if len(warm) < 10 or not np.isfinite(a_ts):
                continue
            thr = float(np.percentile(warm[SIG], PRE_Q))
            pre_s = excess(df, thr, a_ts - w, a_ts)
            ratios = [excess(df, thr, x, x + w) / excess(df, thr, x - w, x)
                      for x in (a_ts - k * w for k in range(2, 2 + args.n_placebo))
                      if x - w >= tp and excess(df, thr, x - w, x) > 0]
            if not ratios or pre_s <= 0:
                continue
            rr.append(float(np.log(excess(df, thr, a_ts, a_ts + w) / pre_s)
                            - np.log(float(np.median(ratios)))))
        if len(rr) >= 6:
            v = np.array(rr)
            _, pw = wilcoxon(v, alternative="two-sided")
            sweep.append({"window_s": w, "n_reps": len(v),
                          "did_ratio_median": round(float(np.exp(np.median(v))), 3),
                          "n_reps_did_positive": int((v > 0).sum()),
                          "p_did": float(f"{pw:.3g}")})
    out["window_sweep"] = sweep

    (PROC / "placebo_control.json").write_text(json.dumps(out, indent=2))
    for scen, r in sham.items():
        print(f"  sham anchor {scen:16s} DiD {r['did_ratio_median']:.2f}x (+{r['n_reps_positive']}/{r['n_reps']})")
    for r in sweep:
        print(f"  window {r['window_s']:>4.0f} s: DiD {r['did_ratio_median']:.2f}x  p = {r['p_did']}")
    e = out["excess"]
    print(f"  overall DiD {e['did_ratio_median']:.2f}x  positive in "
          f"{e['n_reps_did_positive']}/{out['params']['n_reps']}  p = {e['p_did']}")
    for scen, r in out["per_scenario"].items():
        print(f"    {scen:16s} DiD {r['did_ratio_median']:.2f}x "
              f"(+{r['n_reps_did_positive']}/{r['n_reps']})")


if __name__ == "__main__":
    main()
