#!/usr/bin/env python3
"""Surface-versus-magnitude test read on excess mass rather than event counts.

The headline surface result is reported on the per-hour count of cascade events.
In the Open vSwitch case study the page-based signal is censored at the capture
window's ceiling during a cascade, so a count-based null invites the reading
that the flat relationship is a ceiling artifact. This script repeats the test
on the quantity that is not censored the same way: the excess mass above the
per-repetition threshold accumulated over the aftermath window.

Both readings are reported so the two can be compared directly.

Output: data/processed/surface_excess_spearman.json

Usage: python3 scripts/surface_excess_spearman.py [--window 300]
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
AGG = PROC / "ovs_recollection_aggregates"
SIG = "change_volume_sum"
PRE_Q = 95
SURFACE = {"E_single_rule": 1, "F_burst": 21, "D_flush": 200}
SEED = 42


def per_rep(window: float):
    rows = []
    for path in sorted(AGG.glob("*.csv")):
        scen = next((s for s in SURFACE if path.stem.startswith(s)), None)
        if scen is None:
            continue
        df = pd.read_csv(path)
        ws, ca = df["warmup_start_ts"].iloc[0], df["controller_attached_ts"].iloc[0]
        action_ts = float(df["action_ts"].iloc[0])
        warm = df[(df["ts"] >= ws) & (df["ts"] < ca)]
        if len(warm) < 10 or not np.isfinite(action_ts):
            continue
        thr = float(np.percentile(warm[SIG], PRE_Q))
        w = df[(df["ts"] >= action_ts) & (df["ts"] < action_ts + window)]
        if w.empty:
            continue
        sup = w[w[SIG] > thr]
        rows.append({"rep": path.stem, "scenario": scen, "surface": SURFACE[scen],
                     "excess": float((sup[SIG] - thr).sum()),
                     "count": int(len(sup))})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=float, default=300.0)
    args = ap.parse_args()

    df = per_rep(args.window)
    if df.empty:
        raise SystemExit(f"no induced repetitions under {AGG}")

    out = {"params": {"window_s": args.window, "threshold": f"p{PRE_Q} of warmup {SIG}",
                      "surface_levels": SURFACE, "n_reps": int(len(df))},
           "per_scenario": {}, "spearman": {}}
    for metric in ("excess", "count"):
        rho, p = spearmanr(df["surface"], df[metric])
        out["spearman"][metric] = {"rho": round(float(rho), 4), "p_value": round(float(p), 4),
                                   "n": int(len(df))}
    for scen, g in df.groupby("scenario"):
        out["per_scenario"][scen] = {
            "surface": SURFACE[scen], "n_reps": int(len(g)),
            "excess_mean": round(float(g["excess"].mean()), 1),
            "excess_median": round(float(g["excess"].median()), 1),
            "excess_std": round(float(g["excess"].std(ddof=0)), 1),
            "count_mean": round(float(g["count"].mean()), 1),
        }
    df.to_csv(PROC / "surface_excess_per_rep.csv", index=False)
    (PROC / "surface_excess_spearman.json").write_text(json.dumps(out, indent=2))

    for metric, r in out["spearman"].items():
        print(f"Spearman on {metric:7s}: rho = {r['rho']:+.3f}, p = {r['p_value']:.3f}, n = {r['n']}")
    for scen in ("E_single_rule", "F_burst", "D_flush"):
        r = out["per_scenario"][scen]
        print(f"  {scen:16s} surface={r['surface']:3d}  excess mean {r['excess_mean']:10.1f} "
              f"median {r['excess_median']:10.1f}  count mean {r['count_mean']:6.1f}")


if __name__ == "__main__":
    main()
