#!/usr/bin/env python3
"""Interaction between two closely spaced induced actions (reviewer R4.7).

Scenarios G and H deliver a flow-table flush and then a single-rule insertion
inside the flush aftermath, at nominal spacings of 30 s and 150 s (measured 28 s
and 138 s). This script asks whether the second action is still legible on top
of the cascade the first one is still running.

Ruler. The primary reading is the EXCESS mass above the repetition's warmup 95th
percentile, summed over the window. Event counts are reported as a secondary
reading but do not discriminate action from no action in this corpus: with the
controller attached, the pre-action test phase already runs at the same
supra-threshold occupancy as the aftermath (scripts/presence_null.py), while the
excess separates aftermath from pre-action in 29 of 30 induced repetitions.

Design. For each overlap repetition the window of WIN seconds immediately before
the second action is compared with the WIN seconds immediately after it. Both
windows sit on the same first cascade, so the paired within-repetition contrast
controls for where that cascade happens to be. As an independent control, the
same two windows at matched elapsed time are measured on the solo flush
repetitions (scenario D), where no second action is delivered.

Output: data/processed/overlap_analysis.json and overlap_per_rep.csv.

Usage: python3 scripts/overlap_analysis.py [--win 25] [--sweep 10,15,20,25]
"""
import argparse
import csv
import json
import statistics as st
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import mannwhitneyu, wilcoxon

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
AGG = PROC / "ovs_recollection_aggregates"
SIG = "change_volume_sum"
PRE_Q = 95
OVERLAP = {"G_overlap_30s": 30.0, "H_overlap_150s": 150.0}
SOLO = "D_flush"
METRICS = ("excess", "occupancy", "pages")


def threshold_and_frame(path: Path):
    df = pd.read_csv(path)
    ws, ca = df["warmup_start_ts"].iloc[0], df["controller_attached_ts"].iloc[0]
    warm = df[(df["ts"] >= ws) & (df["ts"] < ca)]
    if len(warm) < 10:
        return None, None
    return float(np.percentile(warm[SIG], PRE_Q)), df


def window_metrics(df, thr, lo, hi):
    """Three readings of the same window.

    excess:    summed (signal - threshold) over supra-threshold iterations, the
               quantity that separates aftermath from pre-action phase.
    occupancy: fraction of iterations above the threshold.
    pages:     mean number of mutated pages per iteration.
    """
    w = df[(df["ts"] >= lo) & (df["ts"] < hi)]
    if w.empty:
        return None
    sup = w[w[SIG] > thr]
    return {"excess": float((sup[SIG] - thr).sum()),
            "occupancy": float((w[SIG] > thr).mean()),
            "pages": float(w["n_active"].mean()),
            "n_iters": int(len(w))}


def _row(scenario, rep, before, after, extra):
    row = {"scenario": scenario, "rep": rep}
    row.update(extra)
    for m in METRICS:
        row[f"{m}_before"] = round(before[m], 4)
        row[f"{m}_after"] = round(after[m], 4)
        row[f"delta_{m}"] = round(after[m] - before[m], 4)
    row["n_iters_before"] = before["n_iters"]
    row["n_iters_after"] = after["n_iters"]
    return row


def overlap_rows(win: float):
    rows = []
    for scen in OVERLAP:
        for path in sorted(AGG.glob(f"{scen}_rep*.csv")):
            thr, df = threshold_and_frame(path)
            if df is None or "action2_ts" not in df.columns:
                continue
            a1, a2 = float(df["action_ts"].iloc[0]), float(df["action2_ts"].iloc[0])
            before = window_metrics(df, thr, a2 - win, a2)
            after = window_metrics(df, thr, a2, a2 + win)
            if before is None or after is None:
                continue
            rows.append(_row(scen, path.stem, before, after,
                             {"spacing_s": round(a2 - a1, 2)}))
    return rows


def solo_rows(win: float, elapsed: float):
    """Same two windows on the solo flush reps, at matched elapsed time."""
    rows = []
    for path in sorted(AGG.glob(f"{SOLO}_rep*.csv")):
        thr, df = threshold_and_frame(path)
        if df is None:
            continue
        ref = float(df["action_ts"].iloc[0]) + elapsed
        before = window_metrics(df, thr, ref - win, ref)
        after = window_metrics(df, thr, ref, ref + win)
        if before is None or after is None:
            continue
        rows.append(_row(SOLO, path.stem, before, after,
                         {"elapsed_s": round(elapsed, 2)}))
    return rows


def paired_test(rows, metric):
    b = [r[f"{metric}_before"] for r in rows]
    a = [r[f"{metric}_after"] for r in rows]
    diff = np.array(a) - np.array(b)
    out = {"n_reps": len(rows), "metric": metric,
           "before_median": round(float(np.median(b)), 4),
           "after_median": round(float(np.median(a)), 4),
           "delta_median": round(float(np.median(diff)), 4),
           "n_reps_after_greater": int((diff > 0).sum())}
    if len(rows) >= 6 and np.any(diff != 0):
        stat, p = wilcoxon(a, b, alternative="two-sided", zero_method="wilcox")
        out["wilcoxon_stat"] = round(float(stat), 2)
        out["wilcoxon_p"] = float(f"{p:.3g}")
    return out


def analyse(win, metric):
    rows = overlap_rows(win)
    if not rows:
        raise SystemExit(f"no overlap aggregates under {AGG}; run build_ovs_aggregates.py first")
    res = {"window_s": win, "metric": metric, "per_scenario": {}, "solo_control": {},
           "between_spacings": {}}
    key = f"delta_{metric}"
    for scen in OVERLAP:
        sub = [r for r in rows if r["scenario"] == scen]
        if not sub:
            continue
        r = paired_test(sub, metric)
        r["spacing_measured_median_s"] = round(st.median([x["spacing_s"] for x in sub]), 2)
        r["spacing_nominal_s"] = OVERLAP[scen]
        res["per_scenario"][scen] = r
        ctrl = solo_rows(win, r["spacing_measured_median_s"])
        if ctrl:
            c = paired_test(ctrl, metric)
            c["elapsed_s"] = r["spacing_measured_median_s"]
            u, p = mannwhitneyu([x[key] for x in sub], [x[key] for x in ctrl],
                                alternative="two-sided")
            c["delta_vs_overlap_mannwhitney"] = {"U": float(u), "p": float(f"{p:.3g}")}
            res["solo_control"][scen] = c
    g = [x[key] for x in rows if x["scenario"] == "G_overlap_30s"]
    h = [x[key] for x in rows if x["scenario"] == "H_overlap_150s"]
    if g and h:
        u, p = mannwhitneyu(g, h, alternative="two-sided")
        res["between_spacings"] = {"delta_median_30s": round(float(np.median(g)), 4),
                                   "delta_median_150s": round(float(np.median(h)), 4),
                                   "U": float(u), "p": float(f"{p:.3g}"),
                                   "n_30s": len(g), "n_150s": len(h)}
    return rows, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--win", type=float, default=25.0,
                    help="primary window length in seconds (must fit inside the 28 s spacing)")
    ap.add_argument("--sweep", default="10,15,20,25",
                    help="window lengths swept for robustness")
    args = ap.parse_args()

    rows, primary = analyse(args.win, "excess")
    sweep = [analyse(w, m)[1]
             for w in (float(x) for x in args.sweep.split(","))
             for m in METRICS]

    out = {"params": {"primary_window_s": args.win,
                      "primary_metric": "excess",
                      "iteration_threshold": f"p{PRE_Q} of warmup {SIG}",
                      "metrics": {
                          "excess": "summed (signal - threshold) over supra-threshold iterations",
                          "occupancy": "fraction of supra-threshold iterations in the window",
                          "pages": "mean mutated pages per iteration in the window"},
                      "note": ("Excess is the primary reading because event counts do not "
                               "separate the aftermath from the pre-action test phase in "
                               "this corpus (scripts/presence_null.py). The window sweep is "
                               "reported because a single window length would not show "
                               "whether an effect is stable.")},
           "primary": primary,
           "window_sweep": sweep}

    (PROC / "overlap_analysis.json").write_text(json.dumps(out, indent=2))
    with open(PROC / "overlap_per_rep.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"primary window {args.win} s, metric excess:")
    for scen, r in primary["per_scenario"].items():
        c = primary["solo_control"].get(scen, {})
        print(f"  {scen} (spacing {r['spacing_measured_median_s']} s): "
              f"{r['before_median']:.0f} -> {r['after_median']:.0f} "
              f"(up in {r['n_reps_after_greater']}/{r['n_reps']}, p={r.get('wilcoxon_p')}); "
              f"solo flush control p={c.get('wilcoxon_p')}, "
              f"overlap vs solo p={c.get('delta_vs_overlap_mannwhitney', {}).get('p')}")
    b = primary["between_spacings"]
    if b:
        print(f"  30 s vs 150 s spacing: delta {b['delta_median_30s']:.0f} vs "
              f"{b['delta_median_150s']:.0f}, p={b['p']}")
    print("window sweep (paired p per scenario):")
    for r in sweep:
        ps = {k: v.get("wilcoxon_p") for k, v in r["per_scenario"].items()}
        print(f"  win={r['window_s']:>5} metric={r['metric']:>9}: {ps}")


if __name__ == "__main__":
    main()
