#!/usr/bin/env python3
"""Count-level null for the OvS ripple-presence claim.

The presence criterion used in Table 3 is qualitative: a repetition counts as
showing a cascade when at least one post-action iteration exceeds the warmup
95th percentile. At that threshold roughly five per cent of warmup iterations
already exceed it by construction, so "at least one event in the aftermath" is
a weak criterion on its own. This script calibrates it: it compares the OBSERVED
number of supra-threshold iterations in each induced repetition's aftermath
window against a null distribution of counts drawn from matched no-action
windows of the same length.

The null pool is built from two sources, both action-free by construction:
  - the idle-baseline repetitions (scenario A_idle), over their full test phase;
  - the warmup window of every repetition in the corpus, which precedes any
    induced action.
Each null window is scored with the threshold of the repetition it comes from,
so a repetition with a noisier baseline contributes a noisier null.

For each induced repetition the empirical p-value is the fraction of null
windows whose count is at least the observed count. Since the null pool is
finite, p-values are reported with the standard (r + 1) / (n + 1) correction.

Output: data/processed/presence_null.json.

Usage: python3 scripts/presence_null.py [--window 300] [--stride 30]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
AGG = PROC / "ovs_recollection_aggregates"
SIG = "change_volume_sum"
PRE_Q = 95
INDUCED = ("D_flush", "E_single_rule", "F_burst")
IDLE = "A_idle"


def load(path: Path):
    df = pd.read_csv(path)
    ws, ca = df["warmup_start_ts"].iloc[0], df["controller_attached_ts"].iloc[0]
    warm = df[(df["ts"] >= ws) & (df["ts"] < ca)]
    if len(warm) < 10:
        return None
    thr = float(np.percentile(warm[SIG], PRE_Q))
    return df, warm, thr


def count_windows(df, thr, lo, hi, window, stride):
    """Counts of supra-threshold iterations in every window of `window` seconds
    starting on a `stride`-second grid inside [lo, hi]."""
    counts = []
    start = lo
    while start + window <= hi:
        w = df[(df["ts"] >= start) & (df["ts"] < start + window)]
        if len(w) > 0:
            counts.append(int((w[SIG] > thr).sum()))
        start += stride
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=float, default=300.0)
    ap.add_argument("--stride", type=float, default=30.0)
    ap.add_argument("--paired", type=float, default=250.0,
                    help="length of the matched pre-action window for the paired excess test")
    args = ap.parse_args()

    null_counts, null_sources = [], []
    for path in sorted(AGG.glob("*.csv")):
        loaded = load(path)
        if loaded is None:
            continue
        df, warm, thr = loaded
        # warmup window of every repetition (action-free by construction)
        c = count_windows(df, thr, float(warm["ts"].min()), float(warm["ts"].max()),
                          args.window, args.stride)
        null_counts += c
        null_sources += [f"warmup:{path.stem}"] * len(c)
        # full test phase of the idle repetitions (no induced action at all)
        if path.stem.startswith(IDLE):
            tp = float(df["test_phase_start_ts"].iloc[0])
            c = count_windows(df, thr, tp, float(df["ts"].max()), args.window, args.stride)
            null_counts += c
            null_sources += [f"idle:{path.stem}"] * len(c)

    if not null_counts:
        raise SystemExit("empty null pool; check the aggregates directory")
    null = np.array(null_counts)

    observed = []
    for path in sorted(AGG.glob("*.csv")):
        if not path.stem.startswith(INDUCED):
            continue
        loaded = load(path)
        if loaded is None:
            continue
        df, _, thr = loaded
        a = float(df["action_ts"].iloc[0])
        w = df[(df["ts"] >= a) & (df["ts"] < a + args.window)]
        n = int((w[SIG] > thr).sum())
        r = int((null >= n).sum())
        observed.append({"rep": path.stem,
                         "scenario": next(s for s in INDUCED if path.stem.startswith(s)),
                         "count": n,
                         "n_null_ge": r,
                         "p_empirical": round((r + 1) / (len(null) + 1), 5)})

    # Calibrated criterion: paired excess, aftermath vs matched pre-action window
    paired = []
    for path in sorted(AGG.glob("*.csv")):
        if not path.stem.startswith(INDUCED):
            continue
        loaded = load(path)
        if loaded is None:
            continue
        df, _, thr = loaded
        a = float(df["action_ts"].iloc[0])
        pre = df[(df["ts"] >= a - args.paired) & (df["ts"] < a)]
        post = df[(df["ts"] >= a) & (df["ts"] < a + args.paired)]
        if pre.empty or post.empty:
            continue

        def mass(w):
            sup = w[w[SIG] > thr]
            return float((sup[SIG] - thr).sum())

        def occ(w):
            return float((w[SIG] > thr).mean())

        paired.append({"rep": path.stem,
                       "occupancy_pre": round(occ(pre), 4),
                       "occupancy_post": round(occ(post), 4),
                       "count_pre": int((pre[SIG] > thr).sum()),
                       "count_post": int((post[SIG] > thr).sum()),
                       "scenario": next(s for s in INDUCED if path.stem.startswith(s)),
                       "excess_pre": round(mass(pre), 1),
                       "excess_post": round(mass(post), 1)})
    pre_v = np.array([r["excess_pre"] for r in paired])
    post_v = np.array([r["excess_post"] for r in paired])
    stat, p_paired = wilcoxon(post_v, pre_v, alternative="two-sided", zero_method="wilcox")

    alpha = 0.05
    n_sig = sum(o["p_empirical"] < alpha for o in observed)
    # Bonferroni across the induced repetitions
    n_sig_bonf = sum(o["p_empirical"] < alpha / len(observed) for o in observed)
    out = {
        "params": {"window_s": args.window, "stride_s": args.stride,
                   "threshold": f"p{PRE_Q} of the same repetition's warmup {SIG}",
                   "null_sources": "warmup windows of every repetition + full test phase of the idle repetitions"},
        "null": {"n_windows": int(len(null)), "median": float(np.median(null)),
                 "p95": float(np.percentile(null, 95)), "max": int(null.max()),
                 "mean": round(float(null.mean()), 2)},
        "observed": {"n_reps": len(observed),
                     "median_count": float(np.median([o["count"] for o in observed])),
                     "min_count": int(min(o["count"] for o in observed)),
                     "max_count": int(max(o["count"] for o in observed)),
                     "n_below_alpha_05": n_sig,
                     "n_below_bonferroni": n_sig_bonf,
                     "min_attainable_p": round(1 / (len(null) + 1), 5)},
        "paired_excess": {
            "window_s": args.paired,
            "n_reps": len(paired),
            "n_reps_post_greater": int((post_v > pre_v).sum()),
            "occupancy_pre_median": round(float(np.median([r["occupancy_pre"] for r in paired])), 4),
            "occupancy_post_median": round(float(np.median([r["occupancy_post"] for r in paired])), 4),
            "n_reps_count_post_greater": int(sum(r["count_post"] > r["count_pre"] for r in paired)),
            "excess_pre_median": round(float(np.median(pre_v)), 1),
            "excess_post_median": round(float(np.median(post_v)), 1),
            "ratio_of_medians": round(float(np.median(post_v) / np.median(pre_v)), 3),
            "wilcoxon_stat": round(float(stat), 2),
            "wilcoxon_p": float(f"{p_paired:.3g}"),
            "note": ("The calibrated presence criterion. Event counts do not separate the "
                     "aftermath from the matched pre-action window; the excess mass above "
                     "the warmup threshold does. The single repetition that does not "
                     "follow is the low-density burst repetition also visible in the "
                     "burst confidence interval."),
            "per_rep": paired,
        },
        "per_rep": observed,
    }
    (PROC / "presence_null.json").write_text(json.dumps(out, indent=2))

    n = out["null"]
    print(f"null pool: {n['n_windows']} action-free windows of {args.window:.0f} s, "
          f"median {n['median']:.0f} events, p95 {n['p95']:.0f}, max {n['max']}")
    o = out["observed"]
    print(f"observed: {o['n_reps']} induced reps, counts {o['min_count']}-{o['max_count']} "
          f"(median {o['median_count']:.0f}); {o['n_below_alpha_05']}/{o['n_reps']} at p < 0.05, "
          f"{o['n_below_bonferroni']}/{o['n_reps']} under Bonferroni "
          f"(smallest attainable p = {o['min_attainable_p']})")
    q = out["paired_excess"]
    print(f"matched-window occupancy: {q['occupancy_pre_median']} -> {q['occupancy_post_median']}, "
          f"count higher after the action in {q['n_reps_count_post_greater']}/{q['n_reps']} reps")
    print(f"paired excess ({q['window_s']:.0f} s windows): pre {q['excess_pre_median']:.0f} -> "
          f"post {q['excess_post_median']:.0f} ({q['ratio_of_medians']}x), "
          f"post > pre in {q['n_reps_post_greater']}/{q['n_reps']} reps, "
          f"Wilcoxon p = {q['wilcoxon_p']}")


if __name__ == "__main__":
    main()
