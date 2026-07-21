#!/usr/bin/env python3
"""Lag-resolved, action-free-comparator step profile for the OvS case study.

Why this replaces the earlier persistence reading. A persistence criterion needs
a comparator window as long as the aftermath it is compared against. The action
is delivered a median 288 s into the live test phase, so no same-run comparator
of that length fits before it: any such window contains the action and part of
its aftermath. Duration is therefore read here as a LAG PROFILE, in short
disjoint bins, with every comparator window ending at or before the action.

Construction. For a bin width BIN, the action contrast in lag bin k is

    excess([t_a + k*BIN, t_a + (k+1)*BIN)) / excess([t_a - BIN, t_a))

with a single FIXED reference window immediately before the action. The same
quantity is formed at pre-action anchors placed at t_a - 2*BIN, t_a - 4*BIN and
t_a - 6*BIN, each with its own fixed reference, and the per-repetition trend
estimate is the median of those anchors that can still serve that lag bin without
their comparator window reaching the action. The reported step is the difference in
differences in logs. Holding the reference fixed matters: letting it move with
the lag makes the no-action control itself drift downward (verified: a moving
reference drives the sham arm to 0.80-0.88 with p < 0.01, while the fixed
reference leaves it flat), which would be read as decay of the action effect.

The same estimator is run on the three scenarios that deliver no induced action,
with a sham anchor at the elapsed offset where the induced scenarios deliver
theirs. That arm is the negative control: it must stay at unity.

Output: data/processed/lag_profile.json

Usage: python3 scripts/lag_profile.py [--bin 20] [--n-bins 7]
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
SHAM = ("A_idle", "B_flow_install", "C_ping_sustained")
SHAM_OFFSET_S = 288.0
ANCHOR_MULTIPLES = (2, 4, 6)


def repetitions(prefixes):
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
        anchor = float(df["action_ts"].iloc[0])
        if scen in SHAM or not np.isfinite(anchor):
            anchor = tp + SHAM_OFFSET_S
        yield path.stem, scen, df, thr, anchor, tp


def excess(df, thr, lo, hi):
    w = df[(df["ts"] >= lo) & (df["ts"] < hi)]
    s = w[w[SIG] > thr]
    return float((s[SIG] - thr).sum())


def rep_profile(df, thr, anchor, tp, bin_s, n_bins):
    """log difference in differences per lag bin, or None where not estimable."""
    ref = excess(df, thr, anchor - bin_s, anchor)
    if ref <= 0:
        return None
    out = []
    for k in range(n_bins):
        trend = []
        for m in ANCHOR_MULTIPLES:
            anc = anchor - m * bin_s
            if anc - bin_s < tp:
                continue
            # An anchor can only serve lag bin k if ITS bin-k window still ends at
            # or before the action; otherwise the comparator would contain the
            # treatment, which is the defect this estimator exists to avoid.
            if anc + (k + 1) * bin_s > anchor + 1e-9:
                continue
            anc_ref = excess(df, thr, anc - bin_s, anc)
            if anc_ref <= 0:
                continue
            assert anc + (k + 1) * bin_s <= anchor + 1e-9, (
                "comparator window extends past the action")
            trend.append(excess(df, thr, anc + k * bin_s, anc + (k + 1) * bin_s) / anc_ref)
        obs = excess(df, thr, anchor + k * bin_s, anchor + (k + 1) * bin_s)
        med = float(np.median(trend)) if trend else 0.0
        if not trend or obs <= 0 or med <= 0:
            out.append(None)
            continue
        out.append(float(np.log(obs / ref) - np.log(med)))
    return out


def summarize(profiles, bin_s, n_bins):
    rows = []
    for k in range(n_bins):
        vals = np.array([p[k] for p in profiles if p is not None and p[k] is not None])
        if len(vals) < 6:
            continue
        _, p = wilcoxon(vals, alternative="two-sided")
        rows.append({"lag_lo_s": k * bin_s, "lag_hi_s": (k + 1) * bin_s,
                     "n_reps": int(len(vals)),
                     "step": round(float(np.exp(np.median(vals))), 3),
                     "n_reps_positive": int((vals > 0).sum()),
                     "p": float(f"{p:.3g}")})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", type=float, default=20.0)
    ap.add_argument("--n-bins", type=int, default=7)
    args = ap.parse_args()

    induced, per_scen = [], {s: [] for s in INDUCED}
    for stem, scen, df, thr, anchor, tp in repetitions(INDUCED):
        prof = rep_profile(df, thr, anchor, tp, args.bin, args.n_bins)
        if prof is None:
            continue
        induced.append(prof)
        per_scen[scen].append(prof)
    sham = [p for _, _, df, thr, anchor, tp in repetitions(SHAM)
            if (p := rep_profile(df, thr, anchor, tp, args.bin, args.n_bins)) is not None]

    out = {
        "params": {"bin_s": args.bin, "n_bins": args.n_bins,
                   "reference": "fixed window [action - bin, action)",
                   "anchor_multiples_of_bin": list(ANCHOR_MULTIPLES),
                   "sham_offset_s": SHAM_OFFSET_S,
                   "threshold": f"p{PRE_Q} of warmup {SIG}"},
        "note": ("Every comparator window ends at or before the action, enforced by an "
                 "assertion, so no control interval contains the treatment. An anchor "
                 "serves lag bin k only while its own bin-k window still ends before "
                 "the action, so the far bins are estimated from fewer anchors and the "
                 "profile stops where the anchor set runs out, which is a limit of the "
                 "design and not the end of the effect. Bins are disjoint, so a resolved "
                 "bin is activity in that lag range and not an accumulation from earlier lags."),
        "induced": summarize(induced, args.bin, args.n_bins),
        "sham_no_action": summarize(sham, args.bin, args.n_bins),
        "per_scenario": {s: summarize(v, args.bin, args.n_bins) for s, v in per_scen.items()},
        "bonferroni_alpha": None,
    }
    # Bonferroni threshold for the number of bins actually estimated, so the
    # per-scenario rows can be read against the same rule as the pooled arm.
    n_est = len(out["induced"])
    out["bonferroni_alpha"] = round(0.05 / n_est, 5) if n_est else None
    for rows in [out["induced"], out["sham_no_action"]] + list(out["per_scenario"].values()):
        for r in rows:
            r["survives_bonferroni"] = bool(out["bonferroni_alpha"] and r["p"] < out["bonferroni_alpha"])

    (PROC / "lag_profile.json").write_text(json.dumps(out, indent=2))

    print(f"lag profile, {args.bin:.0f} s disjoint bins, fixed pre-action reference")
    for r, sh in zip(out["induced"], out["sham_no_action"]):
        print(f"  [{r['lag_lo_s']:5.0f},{r['lag_hi_s']:5.0f}) s  induced {r['step']:.2f}x "
              f"({r['n_reps_positive']}/{r['n_reps']}, p={r['p']})   sham {sh['step']:.2f}x (p={sh['p']})")
    for scen, rows in out["per_scenario"].items():
        print(f"  {scen:16s} " + "  ".join(f"{r['step']:.2f}x" for r in rows))


if __name__ == "__main__":
    main()
