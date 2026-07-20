#!/usr/bin/env python3
"""
feature_signature_replevel — repetition-level version of the Fig-6 feature
signature test (reviewer R2#9, second half: "iteration-level tests contain
within-repetition dependence").

The published Fig-6 test (scripts/stats_tests.py::stats_feature_signature,
regen_figs_data.py::build_fig6) pooled ~181 ripple iterations drawn from a
SINGLE flush repetition against ~9570 baseline iterations and compared them
with a Mann-Whitney U that treats iterations as independent. Because the ripple
iterations come from one rep, that is pseudoreplication: the effective sample
is the repetition, not the iteration.

This test moves inference to the repetition. For each INDUCED rep it forms a
paired within-rep contrast: the mean of each feature over that rep's ripple
iterations vs the mean over that rep's own warmup (baseline) iterations, using
the SAME ripple/baseline definitions as Fig-6 (ripple = post-action iterations
in [action, action+AFTERMATH_S] whose change_volume_sum exceeds the warmup 95th percentile;
baseline = warmup-window iterations). Across the N induced reps it runs a
one-sided Wilcoxon signed-rank test (ripple > baseline) per feature, plus a
rep-level bootstrap CI on the ripple/baseline ratio. Pairing within rep removes
the within-rep dependence the reviewer flagged.

The finding (ripple iterations show a larger memory footprint) is NOT re-opened;
this only re-expresses its significance at the correct unit.

Usage:
  feature_signature_replevel.py --snapshots DIR [--aftermath 300] [--out J.json]
"""
import argparse
import json
from math import comb
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

SEED = 42
AFTERMATH_S = 300
SIGNAL_COL = "change_volume_sum"
# 8-column raw per-page schema written by the switch dumper (no header row).
COLS = ["ts", "iter_index", "page_id", "x_res", "y_res",
        "change_volume", "byte_entropy_changed", "byte_entropy_full"]
# The four Fig-6 features (label -> aggregated column).
FEATS = {"Active pages": "n_active", "Volume sum": "change_volume_sum",
         "High-region pages": "region_high", "Entropy (changed)": "entropy_changed_mean"}
INDUCED_PREFIXES = ("D_flush", "E_single_rule", "F_burst")


def per_iter_aggregates(df: pd.DataFrame, max_page: int) -> pd.DataFrame:
    """Canonical Fig-6 per-iteration aggregation (copied verbatim from
    ripples-data/scripts/regen_figs_data.py so the ripple/baseline features
    match the published figure exactly)."""
    aggs = []
    for ts, g in df.groupby("ts"):
        a = g[g["change_volume"] > 0]
        if len(a) == 0:
            continue
        pids = a["page_id"].values
        aggs.append({
            "ts": ts,
            "n_active": len(a),
            "region_high": int((pids >= max_page * 0.75).sum()),
            "change_volume_sum": float(a["change_volume"].sum()),
            "entropy_changed_mean": float(a["byte_entropy_changed"].mean()),
        })
    return pd.DataFrame(aggs)


def action_ts_of(sd: Path):
    ev_path = sd / "events.json"
    if not ev_path.exists():
        return None
    ev = json.loads(ev_path.read_text())
    for e in ev:
        # accept the legacy label too, matching the original find_action_ts,
        # so a legacy-labelled rep is not silently skipped.
        if e.get("action") in ("inject_action", "inject_attack"):
            return float(e["ts"])
    return None


def load_full(sd: Path):
    """Post-action CSV holds the complete warmup->aftermath per-page timeline."""
    cands = sorted(sd.glob("features_switch1_*_post_action.csv"))
    if not cands:
        cands = sorted(sd.glob("features_switch1_*.csv"))
    if not cands:
        return None
    df = pd.read_csv(cands[-1], header=None, names=COLS)
    return df


def rep_contrast(sd: Path, aftermath: float):
    """Returns {feature: (baseline_mean, ripple_mean)} or None if unusable."""
    markers = json.loads((sd / "markers.json").read_text())
    p1_s, p1_e = markers.get("warmup_start_ts"), markers.get("controller_attached_ts")
    if p1_s is None or p1_e is None:
        return None
    action_ts = action_ts_of(sd)
    if action_ts is None:
        return None
    df = load_full(sd)
    if df is None or df.empty:
        return None
    max_page = int(df["page_id"].max())
    warm = per_iter_aggregates(df[(df["ts"] >= p1_s) & (df["ts"] < p1_e)], max_page)
    after = per_iter_aggregates(df[df["ts"] >= action_ts], max_page)
    if len(warm) < 10 or after.empty:
        return None
    threshold = float(np.percentile(warm[SIGNAL_COL], 95))
    ripple = after[(after[SIGNAL_COL] > threshold) &
                   (after["ts"] >= action_ts) & (after["ts"] <= action_ts + aftermath)]
    if ripple.empty:
        return None                                 # no cascade above warmup -> no ripple iters
    return {label: (float(warm[col].fillna(0).mean()), float(ripple[col].fillna(0).mean()))
            for label, col in FEATS.items()}


def boot_ratio_ci(base, rip, rng, n_boot=10000):
    base, rip = np.asarray(base), np.asarray(rip)
    n = len(base)
    ratios = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        b = base[idx].mean()
        if b > 0:
            ratios.append(rip[idx].mean() / b)
    if not ratios:
        return [None, None]
    return [round(float(np.percentile(ratios, 2.5)), 3),
            round(float(np.percentile(ratios, 97.5)), 3)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", type=Path, required=True)
    ap.add_argument("--aftermath", type=float, default=AFTERMATH_S)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    rng = np.random.default_rng(SEED)

    reps = [d for d in sorted(args.snapshots.iterdir())
            if d.is_dir() and d.name.startswith(INDUCED_PREFIXES)
            and (d / "markers.json").exists()]
    per_feat = {label: {"base": [], "rip": [], "reps": []} for label in FEATS}
    used, censored = 0, []
    for sd in reps:
        c = rep_contrast(sd, args.aftermath)
        if c is None:
            censored.append(sd.name)      # scanned but no supra-warmup ripple / unusable
            print(f"  censored {sd.name} (no ripple iters above warmup p95 / short warmup)")
            continue
        used += 1
        for label, (b, r) in c.items():
            per_feat[label]["base"].append(b)
            per_feat[label]["rip"].append(r)
            per_feat[label]["reps"].append(sd.name)

    def sig3(x):                          # keep tiny p-values instead of rounding to 0.0
        return float(f"{x:.3g}")

    out = {"n_induced_reps_used": used, "n_reps_scanned": len(reps),
           "n_censored": len(censored), "censored_reps": censored,
           "aftermath_s": args.aftermath, "seed": SEED,
           # each ripple iter is SELECTED as change_volume_sum > warmup p95, so for
           # the "Volume sum" feature (== that signal) ripple>baseline is definitional;
           # the informative evidence is the three non-selection features.
           "selection_variable_feature": "Volume sum",
           "features": {}}
    for label, d in per_feat.items():
        base, rip = np.array(d["base"]), np.array(d["rip"])
        if len(base) < 6:
            out["features"][label] = {"n": len(base), "note": "too few reps"}
            continue
        # one-sided Wilcoxon signed-rank: ripple > baseline
        stat, p = wilcoxon(rip, base, alternative="greater", zero_method="wilcox")
        diff = rip - base
        n = len(base); n_pos = int((diff > 0).sum()); n_zero = int((diff == 0).sum())
        # When ALL diffs are positive, W+ is the combinatorial maximum n(n+1)/2 and
        # the EXACT one-sided p is 2^-n. Pin it so it never rounds away to 0.
        all_pos = (n_pos == n and n_zero == 0)
        p_exact = 2.0 ** (-n) if all_pos else float(p)
        # conservative sensitivity: treat every censored rep as a non-positive (tie/no
        # effect) outcome and run a one-sided sign test over ALL scanned reps.
        n_scan = used + len(censored)
        p_sign = sum(comb(n_scan, k) for k in range(n_pos, n_scan + 1)) / 2.0 ** n_scan
        out["features"][label] = {
            "n_reps": n,
            "baseline_mean": round(float(base.mean()), 3),
            "ripple_mean": round(float(rip.mean()), 3),
            # median_ratio = median of per-rep ratios; ratio_ci = CI on ratio-of-means
            # (two estimands, they coincide when per-rep variation is small).
            "median_ratio": round(float(np.median(rip[base > 0] / base[base > 0])), 3),
            "ratio_ci_of_means": boot_ratio_ci(base, rip, rng),
            "wilcoxon_stat": round(float(stat), 2),
            "wilcoxon_p": sig3(p_exact),
            "wilcoxon_p_note": ("all reps positive; W+ maximal; exact one-sided p = 2^-n"
                                if all_pos else "scipy signed-rank p"),
            "reps_ripple_gt_baseline": f"{n_pos}/{n}",
            "sign_test_p_incl_censored": sig3(p_sign),
            "definitional": label == "Volume sum",
            "sustained": bool(p_exact < 0.05),
        }

    dest = args.out or (args.snapshots.parent / "analysis" / "feature_signature_replevel.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    print(f"wrote {dest}")


def _selfcheck():
    # synthetic: ripple strictly above baseline in every rep -> Wilcoxon rejects.
    rng = np.random.default_rng(0)
    base = rng.uniform(1, 2, 12); rip = base + rng.uniform(1, 2, 12)
    stat, p = wilcoxon(rip, base, alternative="greater")
    ci = boot_ratio_ci(base, rip, rng)
    assert p < 0.05 and ci[0] > 1.0, (p, ci)
    print("selfcheck ok")


if __name__ == "__main__":
    import sys
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        main()
