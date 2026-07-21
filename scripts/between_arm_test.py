#!/usr/bin/env python3
"""Direct between-arm test: induced repetitions against no-action repetitions.

The placebo-adjusted step is tested in two ways, and they answer different
questions. The within-arm test asks whether the step differs from zero in the
induced repetitions. That is necessary but not sufficient, because the estimator
itself could produce a positive value wherever it is applied. The between-arm
test asks the question the design is meant to answer: does the estimator return
a larger value where an action was delivered than where none was?

Both arms use the identical estimator: a fixed pre-action reference window, trend
anchors spaced two window lengths back, and the difference in differences of the
logs. IMPORTANT, and stated in the paper the same way: the two arms are DIFFERENT
SCENARIOS, not the same scenario with the action toggled. The induced arm is
flush, single-rule and burst; the no-action arm is idle, routine rule
installation and sustained traffic. The comparison therefore bounds a generic
elapsed-time artifact of the estimator; it does not isolate action delivery as
the only difference between the arms, and it is not a matched action-withheld
counterfactual. Reading it as one requires assuming the two scenario groups
share a common trend, which this corpus cannot verify. The test is unpaired and
is run with a rank test at the repetition level.

Output: data/processed/between_arm_test.json

Usage: python3 scripts/between_arm_test.py [--win 30]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
AGG = PROC / "ovs_recollection_aggregates"
SIG = "change_volume_sum"
PRE_Q = 95
INDUCED = ("D_flush", "E_single_rule", "F_burst")
SHAM = ("A_idle", "B_flow_install", "C_ping_sustained")
SHAM_OFFSET_S = 288.0
ANCHOR_MULTIPLES = (2, 4, 6)


def excess(df, thr, lo, hi):
    w = df[(df["ts"] >= lo) & (df["ts"] < hi)]
    s = w[w[SIG] > thr]
    return float((s[SIG] - thr).sum())


def arm(prefixes, win, use_sham_anchor):
    """log difference in differences per repetition, one arm."""
    out = []
    for path in sorted(AGG.glob("*.csv")):
        scen = next((s for s in prefixes if path.stem.startswith(s)), None)
        if scen is None:
            continue
        df = pd.read_csv(path)
        ws, ca = df["warmup_start_ts"].iloc[0], df["controller_attached_ts"].iloc[0]
        warm = df[(df["ts"] >= ws) & (df["ts"] < ca)]
        if len(warm) < 10:
            continue
        thr = float(np.percentile(warm[SIG], PRE_Q))
        tp = float(df["test_phase_start_ts"].iloc[0])
        if use_sham_anchor:
            anchor = tp + SHAM_OFFSET_S
        else:
            anchor = float(df["action_ts"].iloc[0])
            if not np.isfinite(anchor):
                continue
        pre = excess(df, thr, anchor - win, anchor)
        trend = [excess(df, thr, x, x + win) / excess(df, thr, x - win, x)
                 for x in (anchor - m * win for m in ANCHOR_MULTIPLES)
                 if x - win >= tp and excess(df, thr, x - win, x) > 0]
        if pre <= 0 or not trend:
            continue
        out.append({"rep": path.stem, "scenario": scen,
                    "log_did": float(np.log(excess(df, thr, anchor, anchor + win) / pre)
                                     - np.log(float(np.median(trend))))})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--win", type=float, default=30.0)
    args = ap.parse_args()

    ind = arm(INDUCED, args.win, use_sham_anchor=False)
    sha = arm(SHAM, args.win, use_sham_anchor=True)
    vi = np.array([r["log_did"] for r in ind])
    vs = np.array([r["log_did"] for r in sha])
    u, p = mannwhitneyu(vi, vs, alternative="two-sided")

    out = {
        "params": {"window_s": args.win, "anchor_multiples_of_window": list(ANCHOR_MULTIPLES),
                   "sham_offset_s": SHAM_OFFSET_S, "threshold": f"p{PRE_Q} of warmup {SIG}"},
        "note": ("Both arms use the identical estimator, but they are different scenarios, "
                 "not the same scenario with the action withheld: induced is flush, "
                 "single-rule and burst; no-action is idle, routine rule installation "
                 "and sustained traffic. The contrast bounds a generic elapsed-time "
                 "artifact of the estimator and is not a matched counterfactual."),
        "induced_arm": {"n_reps": len(vi), "step_median": round(float(np.exp(np.median(vi))), 3)},
        "no_action_arm": {"n_reps": len(vs), "step_median": round(float(np.exp(np.median(vs))), 3)},
        "between_arm": {"mann_whitney_u": float(u), "p": float(f"{p:.3g}"),
                        "ratio_of_medians": round(float(np.exp(np.median(vi) - np.median(vs))), 3)},
        "per_rep": {"induced": ind, "no_action": sha},
    }
    (PROC / "between_arm_test.json").write_text(json.dumps(out, indent=2))

    print(f"induced   n={len(vi):2d}  median step {out['induced_arm']['step_median']}x")
    print(f"no action n={len(vs):2d}  median step {out['no_action_arm']['step_median']}x")
    b = out["between_arm"]
    print(f"between arms: U = {b['mann_whitney_u']:.0f}, p = {b['p']}, "
          f"ratio of medians {b['ratio_of_medians']}x")


if __name__ == "__main__":
    main()
