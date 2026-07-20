#!/usr/bin/env python3
"""Per-scenario within-cosine bootstrap CIs (repetition level).

Figure 2(b) plots the mean within-scenario signature cosine per OvS scenario
with a 95% interval. That interval must come from the released data rather than
from a constant in the plotting code, so it is computed here and written to
data/processed/within_scenario_ci.json, which the figure code reads.

Resampling follows scripts/signature_replevel_perm.py: repetitions, not pairs,
are the unit. Within a scenario the repetitions are resampled with replacement,
a pair contributes mult[a] * mult[b] times, and the statistic is the weighted
mean cosine over pairs whose two endpoints are both in the draw.

Usage: python3 scripts/within_scenario_ci.py [--n-boot 10000]
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
PAIRS = PROC / "signature_pairwise_similarity.csv"
SEED = 42


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-boot", type=int, default=10000)
    args = ap.parse_args()
    rng = np.random.default_rng(SEED)

    df = pd.read_csv(PAIRS)
    same = df[df["same_scenario"].astype(str).str.lower() == "true"]

    out = {"params": {"n_boot": args.n_boot, "seed": SEED,
                      "unit": "repetition (pairs weighted by endpoint multiplicity)",
                      "source": "data/processed/signature_pairwise_similarity.csv"},
           "per_scenario": {}}

    for scenario, g in same.groupby("scenario_a"):
        reps = sorted(set(g["rep_a"]) | set(g["rep_b"]))
        idx = {r: i for i, r in enumerate(reps)}
        a = g["rep_a"].map(idx).to_numpy()
        b = g["rep_b"].map(idx).to_numpy()
        v = g["cosine_similarity"].to_numpy()
        means = []
        for _ in range(args.n_boot):
            take = rng.choice(len(reps), size=len(reps), replace=True)
            mult = np.bincount(take, minlength=len(reps))
            w = mult[a] * mult[b]
            if w.sum() == 0:
                continue
            means.append(float((v * w).sum() / w.sum()))
        out["per_scenario"][scenario] = {
            "n_reps": len(reps),
            "n_pairs": int(len(g)),
            "within_mean": round(float(v.mean()), 4),
            "ci95": [round(float(np.percentile(means, 2.5)), 4),
                     round(float(np.percentile(means, 97.5)), 4)],
        }

    (PROC / "within_scenario_ci.json").write_text(json.dumps(out, indent=2))
    for scenario, r in sorted(out["per_scenario"].items()):
        print(f"{scenario:14s} n_reps={r['n_reps']:3d} mean={r['within_mean']:.3f} "
              f"CI95={r['ci95']}")


if __name__ == "__main__":
    main()
