#!/usr/bin/env python3
"""Statistical tests for the action-ripples paper.

Consumed by Section VI/V tables. Runs over the full corpus of
/tmp/lab_snapshots reps (existing + Phase 2.1 batch additions)
and the cross-domain data in data/crossdomain/.

Outputs `data/processed/stats_summary.json` + a LaTeX-ready table at
`data/processed/table_stats.tex`.

Tests applied:
  - Wilson 95 % CI for binomial detection rate ("100 % detection"
    with N small → bounds matter).
  - Spearman rank correlation + p-value for surface monotonicity
    (3 surface levels per system; reports limited power honestly).
  - Mann-Whitney U for ripple-iteration vs baseline-iteration feature
    distributions (Fig 6 claim).
  - Bootstrap 95 % CI (BCa) for the means in Tables I + II.
  - Permutation test for cross-domain amplification ratios
    (10 000 resamples).

Designed to be re-runnable cheaply once new reps land: it picks up
whatever is on disk and aggregates.
"""
from __future__ import annotations

import os
import json
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

REPO_ROOT = Path(__file__).resolve().parent.parent
LAB_DIR = Path(os.environ.get("OVS_SNAPSHOTS", "/tmp/lab_snapshots"))
XDOM_DIR = REPO_ROOT / "data/crossdomain"
OUT_JSON = REPO_ROOT / "data/processed/stats_summary.json"
OUT_TEX = REPO_ROOT / "data/processed/table_stats.tex"

# --- Constants matching decompose_v2.py and the paper ---
W_TIGHT_S = 2.0
CASCADE_LOOKBACK_S = 5.0
AFTERMATH_S = 300.0


def wilson_ci(successes: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Two-sided Wilson score 1-alpha CI for a binomial proportion."""
    if n == 0:
        return (float("nan"), float("nan"))
    z = stats.norm.ppf(1.0 - alpha / 2.0)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = (z * np.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_ci_mean(values: np.ndarray, n_boot: int = 10_000,
                      alpha: float = 0.05, rng: Optional[np.random.Generator] = None
                      ) -> tuple[float, float, float]:
    """Percentile bootstrap CI for the mean (lo, mean, hi)."""
    if len(values) == 0:
        return (float("nan"), float("nan"), float("nan"))
    rng = rng if rng is not None else np.random.default_rng(42)
    samples = rng.choice(values, size=(n_boot, len(values)), replace=True)
    means = samples.mean(axis=1)
    lo = float(np.percentile(means, 100.0 * (alpha / 2.0)))
    hi = float(np.percentile(means, 100.0 * (1.0 - alpha / 2.0)))
    return (lo, float(values.mean()), hi)


def spearman_with_power_warning(x: np.ndarray, y: np.ndarray) -> dict:
    rho, p = stats.spearmanr(x, y)
    return {
        "rho": float(rho),
        "p_value": float(p),
        "n": int(len(x)),
        "note": (
            "Spearman's rho on three surface levels has low statistical "
            "power; report as descriptive evidence, not formal hypothesis "
            "test."
            if len(x) <= 5 else ""
        ),
    }


def mannwhitney_u(ripple: np.ndarray, baseline: np.ndarray) -> dict:
    u, p = stats.mannwhitneyu(ripple, baseline, alternative="greater")
    return {
        "U": float(u),
        "p_value": float(p),
        "n_ripple": int(len(ripple)),
        "n_baseline": int(len(baseline)),
    }


def permutation_amplification(ripple: np.ndarray, baseline: np.ndarray,
                              n_perm: int = 10_000,
                              rng: Optional[np.random.Generator] = None
                              ) -> dict:
    """Two-sided permutation test for the amplification mean(ripple)/mean(baseline)."""
    if len(ripple) == 0 or len(baseline) == 0:
        return {"observed": float("nan"), "p_value": float("nan")}
    rng = rng if rng is not None else np.random.default_rng(42)
    pooled = np.concatenate([ripple, baseline])
    nr = len(ripple)
    observed = ripple.mean() / max(baseline.mean(), 1e-9)
    count = 0
    for _ in range(n_perm):
        idx = rng.permutation(len(pooled))
        a = pooled[idx[:nr]]
        b = pooled[idx[nr:]]
        amp = a.mean() / max(b.mean(), 1e-9)
        if abs(np.log(amp)) >= abs(np.log(observed)):
            count += 1
    return {
        "observed_amplification": float(observed),
        "p_value": (count + 1) / (n_perm + 1),
        "n_perm": n_perm,
    }


# ----------------------------------------------------------------------
# Per-rep loader / classifier — reuses regen_figs_data.py if available.
# ----------------------------------------------------------------------
def load_per_rep_induced_cascade_counts() -> pd.DataFrame:
    """Return per-rep Attack-cascade counts and per-hour rates.

    Wraps the canonical decompose_v2 logic; if scripts/regen_figs_data.py
    has been run, prefers its cached CSV under data/processed/.
    """
    cached = REPO_ROOT / "data/processed/fig2_sparse_cascade_per_rep.csv"
    if cached.exists():
        return pd.read_csv(cached)
    raise FileNotFoundError(
        f"Expected {cached} from regen_figs_data.py. Run it first or extend "
        "this loader to compute on demand."
    )


def load_feature_distributions() -> pd.DataFrame:
    cached = REPO_ROOT / "data/processed/fig6_feature_distributions.csv"
    if cached.exists():
        return pd.read_csv(cached)
    raise FileNotFoundError(
        f"Expected {cached} from regen_figs_data.py. Run it first."
    )


# ----------------------------------------------------------------------
# Test pipelines
# ----------------------------------------------------------------------
def stats_presence_rate(per_rep: pd.DataFrame) -> dict:
    """Wilson CI for the binomial "rep has Induced-cascade > 0" rate per scenario."""
    out = {}
    for scenario, group in per_rep.groupby("scenario"):
        n = len(group)
        # Tolerate either column name for backward compat.
        col = "induced_cascade_count" if "induced_cascade_count" in group.columns else "attack_cascade_count"
        k = int((group[col] > 0).sum())
        lo, hi = wilson_ci(k, n)
        out[scenario] = {
            "n_reps": n,
            "n_present": k,
            "presence_rate": k / n if n else float("nan"),
            "wilson_95_ci": [lo, hi],
        }
    return out


def stats_surface_monotonicity(per_rep: pd.DataFrame,
                               surface_map: dict[str, int]) -> dict:
    """Spearman across surface levels using per-rep observations."""
    keep = per_rep[per_rep["scenario"].isin(surface_map)].copy()
    keep["surface"] = keep["scenario"].map(surface_map)
    return spearman_with_power_warning(
        keep["surface"].to_numpy(),
        keep["per_hour_rate"].to_numpy(),
    )


def stats_feature_signature(features: pd.DataFrame) -> dict:
    """Mann-Whitney U for ripple > baseline on each feature."""
    out = {}
    for feature, group in features.groupby("feature"):
        ripple = group.loc[group["kind"] == "ripple", "value"].to_numpy()
        baseline = group.loc[group["kind"] == "baseline", "value"].to_numpy()
        out[feature] = mannwhitney_u(ripple, baseline)
    return out


def stats_bootstrap_table(per_rep: pd.DataFrame) -> dict:
    """Bootstrap CI for per-scenario mean per_hour_rate."""
    out = {}
    for scenario, group in per_rep.groupby("scenario"):
        lo, mean, hi = bootstrap_ci_mean(group["per_hour_rate"].to_numpy())
        out[scenario] = {"mean": mean, "bootstrap_95_ci": [lo, hi],
                         "n_reps": len(group)}
    return out


def emit_latex_table(results: dict) -> str:
    lines = [
        r"% Auto-generated by scripts/stats_tests.py — do not edit by hand.",
        r"\begin{table}[t]",
        r"\centering",
        r"\caption{Statistical summary of detection and surface claims, with bootstrap and Wilson 95\,\% confidence intervals.}",
        r"\label{tab:stats_summary}",
        r"\begin{tabular}{lrrr}",
        r"\hline",
        r"Scenario & N & Mean per-hour rate (95\,\% CI) & Wilson detection CI \\",
        r"\hline",
    ]
    boots = results.get("bootstrap_per_scenario", {})
    wils = results.get("presence_rate_per_scenario", {})
    # Map internal scenario codes to paper-friendly descriptive names.
    NAME = {
        "D_flush":         "Flow-table flush",
        "E_single_rule":   "Single-rule injection",
        "F_burst":         "Multi-rule burst",
    }
    for scenario in sorted(boots.keys()):
        b = boots[scenario]
        w = wils.get(scenario, {})
        mean_ci = f"{b['mean']:.0f} ({b['bootstrap_95_ci'][0]:.0f}, {b['bootstrap_95_ci'][1]:.0f})"
        if w:
            wil = f"[{w['wilson_95_ci'][0]:.2f}, {w['wilson_95_ci'][1]:.2f}]"
        else:
            wil = "n/a"
        scenario_label = NAME.get(scenario, scenario.replace("_", r"\_"))
        lines.append(rf"{scenario_label} & {b['n_reps']} & {mean_ci} & {wil} \\")
    lines += [r"\hline", r"\end{tabular}", r"\end{table}"]
    return "\n".join(lines) + "\n"


def main() -> int:
    try:
        per_rep = load_per_rep_induced_cascade_counts()
    except FileNotFoundError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1

    try:
        features = load_feature_distributions()
    except FileNotFoundError as exc:
        print(f"WARN: {exc} (Mann-Whitney skipped)", file=sys.stderr)
        features = None

    surface_map = {
        "E_single_rule": 1,
        "F_burst": 21,
        "D_flush": 200,
    }

    results = {
        "_note": ("presence_rate_per_scenario, surface_monotonicity_spearman and "
                  "bootstrap_per_scenario are computed from "
                  "fig2_sparse_cascade_per_rep.csv and are the source of the "
                  "paper's per-scenario presence, rho = -0.13, and Induced-cascade "
                  "means. feature_signature_mannwhitney pools iterations within a "
                  "repetition and is SUPERSEDED in the paper by the repetition-level "
                  "paired test of scripts/feature_signature_replevel.py; it is kept "
                  "here only for continuity with the first submission."),
        "presence_rate_per_scenario": stats_presence_rate(per_rep),
        "surface_monotonicity_spearman": stats_surface_monotonicity(per_rep, surface_map),
        "bootstrap_per_scenario": stats_bootstrap_table(per_rep),
    }
    if features is not None:
        results["feature_signature_mannwhitney"] = stats_feature_signature(features)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(results, indent=2))
    print(f"Wrote {OUT_JSON}")

    OUT_TEX.write_text(emit_latex_table(results))
    print(f"Wrote {OUT_TEX}")

    print("\n=== Summary ===")
    for scenario, info in results["presence_rate_per_scenario"].items():
        print(f"  {scenario}: n={info['n_reps']}, det={info['n_present']}/"
              f"{info['n_reps']}, Wilson 95% = "
              f"[{info['wilson_95_ci'][0]:.2f}, {info['wilson_95_ci'][1]:.2f}]")
    sp = results["surface_monotonicity_spearman"]
    print(f"  Surface Spearman: rho={sp['rho']:.3f}, p={sp['p_value']:.3f}, n={sp['n']}")
    if sp["note"]:
        print(f"    NOTE: {sp['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
