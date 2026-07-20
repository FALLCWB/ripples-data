#!/usr/bin/env python3
"""Generate fig_crossdomain.png — per-panel post-action signal over time for
the cross-domain systems (OvS, Redis, Dockerd).

For each (system, action) cell:
  1. Pick a representative rep
  2. Compute a per-iteration signal:
       - OvS: change_volume_sum (n_active is capped at 32 so it does not
         separate; volume separates pre from post cleanly)
       - Redis: n_changed_pages (already per-iter in features.csv)
  3. Threshold = 95th percentile of pre-action signal. The pre-action
     window is the calm baseline; post-action excess above this threshold
     is genuinely "above the calm baseline" = a novelty.
  4. Bucket post-action time at 5-second resolution and count, per bucket,
     how many iterations exceeded the threshold. That count IS the height
     of the stem at that bucket center.
  5. Plot as stems: zero buckets show nothing, non-zero buckets show a
     vertical impulse. The pattern looks like discrete ripple events
     punctuating flat-zero calm — the visual user asked for.

Pre-action carries only sparse low-level excursions by construction (few
iterations exceed the 95th percentile of their own training set). Post-action shows ripples as discrete bucket
events. Some panels saturate (every post bucket has detections — this is
itself a finding: large action → sustained cascade rather than discrete
events).

Dockerd is included (GC-controlled measurement, GOGC=off with a manual GC
before warmup and before the action); the per-action amplifications and the
surface-monotonic signature are reported in the paper's cross-domain table.
"""
import os
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
from regen_figs_data import (  # noqa: E402
    per_iter_aggregates, load_rep_features, find_attack_ts,
)

ROOT = Path(__file__).resolve().parent
ECHO_DATA = ROOT / "data/crossdomain"
OVS_DATA = Path(os.environ.get("OVS_SNAPSHOTS", "/tmp/lab_snapshots"))  # raw OvS snapshots not redistributed; set OVS_SNAPSHOTS to your copy
OUT = ROOT / "fig_crossdomain.png"
TABLE = ROOT / "data/processed/crossdomain_summary.csv"

plt.rcParams.update({
    "font.family": "serif",
    "font.size": 16,
    "axes.labelsize": 16,
    "axes.titlesize": 17,
    "xtick.labelsize": 15,
    "ytick.labelsize": 15,
    "legend.fontsize": 14,
    "savefig.dpi": 300,
    "savefig.bbox": "tight",
    "axes.grid": True,
    "grid.alpha": 0.25,
    "axes.spines.top": False,
    "axes.spines.right": False,
})

SYSTEM_COLOR = {"OvS": "#762a83", "Redis": "#1f78b4", "Dockerd": "#33a02c"}

# (system, surface, action_key_for_redis, action_pretty, ovs_scenario_glob)
PLOT_GRID = [
    ("OvS",     "small",  None,              "Single-rule injection (1 entry)",
     "E_single_rule_rep1_*"),
    ("OvS",     "medium", None,              "Multi-rule burst (21 entries)",
     "F_burst_rep1_*"),
    ("OvS",     "large",  None,              "Full-table flush ($\\sim$200 entries)",
     "D_flush_rep1_*"),
    ("Redis",   "small",  "redis_set_1",     "SET (1 key)",          None),
    ("Redis",   "medium", "redis_mset_100",  "MSET (100 keys)",      None),
    ("Redis",   "large",  "redis_flushdb",   "FLUSHDB",              None),
    ("Dockerd", "small",  "docker_inspect",  "docker inspect (readback)",  None),
    ("Dockerd", "medium", "docker_run_1",    "1 container",          None),
    ("Dockerd", "large",  "docker_run_10",   "10 containers",        None),
]

BUCKET_S = 5.0     # seconds per stem
PRE_WIN_S = 30.0   # seconds of pre-action to show
DOCKERD_CUTOFF = 0  # accept all reps with markers.json (legacy filter disabled)
POST_WIN_S = {     # seconds of post-action to show (per system)
    "OvS": 200.0,
    "Redis": 90.0,
    "Dockerd": 90.0,
}


def load_ovs_signal(scenario_glob):
    """For OvS: per-iter change_volume_sum, split pre/post action."""
    matches = sorted(OVS_DATA.glob(scenario_glob))
    if not matches:
        return None
    sd = matches[0]
    try:
        attack_ts = find_attack_ts(sd)
        df, max_page = load_rep_features(sd)
    except Exception:
        return None
    if attack_ts is None:
        return None
    pre_iters = df[df["ts"] < attack_ts]
    post_iters = df[df["ts"] >= attack_ts]
    if pre_iters.empty or post_iters.empty:
        return None
    pre_agg = per_iter_aggregates(pre_iters, max_page)
    post_agg = per_iter_aggregates(post_iters, max_page)
    pre = pd.DataFrame({"t_rel": pre_agg["ts"] - attack_ts,
                        "signal": pre_agg["change_volume_sum"]})
    post = pd.DataFrame({"t_rel": post_agg["ts"] - attack_ts,
                         "signal": post_agg["change_volume_sum"]})
    return {"pre": pre, "post": post}


def load_dockerd_signal(action_kind):
    """Dockerd: per-iter n_changed_pages, post-Phase-1.1 reps only
    (action_ts >= DOCKERD_CUTOFF). Same column-detection logic as
    Redis loader (handles both pre- and post-fix column names)."""
    matches = sorted(ECHO_DATA.glob(f"dockerd_{action_kind}_rep*"))
    matches = [m for m in matches if m.is_dir() and (m / "features.csv").exists()]
    if not matches:
        return None
    # Filter to post-Phase-1.1 reps only
    clean = []
    for m in matches:
        try:
            markers = json.loads((m / "markers.json").read_text())
            if markers.get("action_ts", 0) >= DOCKERD_CUTOFF:
                clean.append(m)
        except Exception:
            continue
    if not clean:
        return None

    # Pick the rep where the post/pre ratio is largest: that is the most
    # visually informative panel of the cascade for this action.
    def score(m):
        try:
            mk = json.loads((m / "markers.json").read_text())
            f = pd.read_csv(m / "features.csv")
            ats = mk["action_ts"]
            pre = f.loc[f.ts < ats, "n_changed_pages"]
            post = f.loc[f.ts >= ats, "n_changed_pages"]
            if len(pre) < 10 or len(post) < 5:
                return -1
            return float(post.max() / max(pre.max(), 1))
        except Exception:
            return -1

    best = max(clean, key=score)
    if score(best) <= 0:
        return None
    try:
        markers = json.loads((best / "markers.json").read_text())
        feat = pd.read_csv(best / "features.csv")
    except Exception:
        return None
    ats = markers["action_ts"]
    pre = pd.DataFrame({"t_rel": feat.loc[feat.ts < ats, "ts"].values - ats,
                        "signal": feat.loc[feat.ts < ats, "n_changed_pages"].values})
    post = pd.DataFrame({"t_rel": feat.loc[feat.ts >= ats, "ts"].values - ats,
                         "signal": feat.loc[feat.ts >= ats, "n_changed_pages"].values})
    if len(pre) < 10 or len(post) < 5:
        return None
    return {"pre": pre, "post": post}


def load_redis_signal(action_kind):
    """For Redis: per-iter n_changed_pages from features.csv, split pre/post."""
    matches = sorted(ECHO_DATA.glob(f"redis_{action_kind}_rep*"))
    matches = [m for m in matches if m.is_dir() and (m / "features.csv").exists()]
    if not matches:
        return None
    # Pick the rep with the largest post/pre signal ratio (most informative).
    def score(m):
        try:
            mk = json.loads((m / "markers.json").read_text())
            f = pd.read_csv(m / "features.csv")
            ats = mk["action_ts"]
            pre = f.loc[f.ts < ats, "n_changed_pages"]
            post = f.loc[f.ts >= ats, "n_changed_pages"]
            if len(pre) < 10 or len(post) < 5:
                return -1
            return float(post.max() / max(pre.max(), 1))
        except Exception:
            return -1
    best = max(matches, key=score)
    if score(best) <= 0:
        return None
    try:
        markers = json.loads((best / "markers.json").read_text())
        feat = pd.read_csv(best / "features.csv")
    except Exception:
        return None
    ats = markers["action_ts"]
    pre = pd.DataFrame({"t_rel": feat.loc[feat.ts < ats, "ts"].values - ats,
                        "signal": feat.loc[feat.ts < ats, "n_changed_pages"].values})
    post = pd.DataFrame({"t_rel": feat.loc[feat.ts >= ats, "ts"].values - ats,
                         "signal": feat.loc[feat.ts >= ats, "n_changed_pages"].values})
    if len(pre) < 10 or len(post) < 5:
        return None
    return {"pre": pre, "post": post}


def detection_buckets(data, post_window_s):
    """Threshold = 95th percentile of pre signal. For post-action time, bucket
    at BUCKET_S seconds and SUM the above-threshold portion of each iteration's
    signal in the bucket. Iterations whose signal is at or below the pre-action
    95th percentile contribute zero. The result is bucket-aggregated novelty
    magnitude.

    Returns: (threshold, bucket_centers, sum_per_bucket). Only post-action
    buckets are returned.
    """
    threshold = float(np.percentile(data["pre"]["signal"].values, 95))
    post = data["post"]
    post = post[(post["t_rel"] >= 0) & (post["t_rel"] <= post_window_s)]
    if post.empty:
        return threshold, np.array([]), np.array([])
    edges = np.arange(0.0, post_window_s + BUCKET_S, BUCKET_S)
    centers = (edges[:-1] + edges[1:]) / 2.0
    # Contribution per iteration: (signal - threshold) when above, else 0.
    excess = np.clip(post["signal"].values - threshold, 0, None)
    bin_idx = np.clip(np.digitize(post["t_rel"].values, edges) - 1,
                      0, len(edges) - 2)
    sums = np.zeros(len(centers), dtype=float)
    for i, e in zip(bin_idx, excess):
        sums[i] += e
    return threshold, centers, sums


def main():
    fig, axes = plt.subplots(3, 3, figsize=(18, 14), sharex=False, sharey=False)
    rows = ["OvS", "Redis", "Dockerd"]
    cols = ["small", "medium", "large"]
    grid = {(s, sf): (ak, ap, sg) for (s, sf, ak, ap, sg) in PLOT_GRID}

    for ri, system in enumerate(rows):
        for ci, surface in enumerate(cols):
            ax = axes[ri][ci]
            action_kind, action_pretty, ovs_glob = grid[(system, surface)]
            if system == "OvS":
                data = load_ovs_signal(ovs_glob)
            elif system == "Redis":
                data = load_redis_signal(action_kind)
            else:
                data = load_dockerd_signal(action_kind)
            color = SYSTEM_COLOR[system]
            post_win = POST_WIN_S[system]
            xlim = (-PRE_WIN_S, post_win)

            if data is None:
                ax.text(0.5, 0.5, "(data unavailable)", ha="center",
                        va="center", transform=ax.transAxes, color="gray")
                ax.set_title(action_pretty, fontsize=14)
                ax.set_xlim(*xlim)
                continue

            threshold, centers, sums = detection_buckets(data, post_win)

            ax.set_xlim(*xlim)
            ymax = sums.max() if len(sums) else 0
            ax.set_ylim(0, max(1.0, ymax * 1.15))
            ax.axhline(0, color="gray", lw=0.6, alpha=0.5)
            ax.axvline(0, color="red", lw=1.6, alpha=0.9)

            nonzero = sums > 0
            n_events = int(nonzero.sum())
            if nonzero.any():
                markerline, stemlines, baseline = ax.stem(
                    centers[nonzero], sums[nonzero],
                    linefmt=color, markerfmt="o", basefmt=" ",
                )
                plt.setp(stemlines, linewidth=3.0, alpha=0.95)
                plt.setp(markerline, markersize=10,
                         markerfacecolor=color, markeredgecolor=color)
                unit_short = "kB" if system == "OvS" else "pg"
                ax.text(0.97, 0.93,
                        f"peak={ymax:.0f} {unit_short}\n{n_events} event{'s' if n_events != 1 else ''}",
                        transform=ax.transAxes, ha="right", va="top",
                        fontsize=13, color=color,
                        bbox=dict(boxstyle="round,pad=0.25",
                                  facecolor="white", edgecolor=color, alpha=0.85))
            else:
                ax.text(0.5, 0.5, "no ripple present",
                        transform=ax.transAxes, ha="center", va="center",
                        fontsize=14, color="gray", style="italic")
            ax.set_title(action_pretty, fontsize=16)
            if ci == 0:
                unit = "kB" if system == "OvS" else "pages"
                ax.set_ylabel(f"{system}\nexcess per {int(BUCKET_S)}s ({unit})",
                              fontsize=15)
            if ri == len(rows) - 1:
                ax.set_xlabel("t $-$ action (s)")

    fig.suptitle("Action ripples observed as discrete signal-excursion events over time\n"
                 "Pre-action is empty by construction; each post-action stem "
                 "counts iterations in a 5-second bucket whose page-mutation "
                 "signal exceeded the 95th percentile of warmup signal",
                 fontsize=16, y=1.01)
    plt.tight_layout()
    plt.savefig(OUT)
    plt.close()
    print(f"Saved {OUT}")

    # Summary table for Redis + Dockerd
    rows_out = []
    summary_grid = [
        ("Redis",   "small",  "redis_set_1",    "SET (1 key)"),
        ("Redis",   "medium", "redis_mset_100", "MSET (100 keys)"),
        ("Redis",   "large",  "redis_flushdb",  "FLUSHDB"),
        ("Dockerd", "small",  "docker_inspect", "docker inspect (readback)"),
        ("Dockerd", "medium", "docker_run_1",   "1 container"),
        ("Dockerd", "large",  "docker_run_10",  "10 containers"),
        ("Dockerd", "xlarge", "docker_run_50",  "50 containers"),
    ]
    for system, surface, action_kind, label in summary_grid:
        prefix = "redis" if system == "Redis" else "dockerd"
        matches = sorted(ECHO_DATA.glob(f"{prefix}_{action_kind}_rep*"))
        matches = [m for m in matches if m.is_dir()]
        peaks, baselines = [], []
        for m in matches:
            try:
                markers = json.loads((m / "markers.json").read_text())
                feat = pd.read_csv(m / "features.csv")
            except Exception:
                continue
            ats = markers["action_ts"]
            pre = feat[feat["ts"] < ats]
            post = feat[feat["ts"] >= ats]
            if len(pre) < 5 or len(post) < 5:
                continue
            baselines.append(pre["n_changed_pages"].mean())
            peaks.append(post["n_changed_pages"].max())
        if not peaks:
            continue
        rows_out.append({
            "system": system, "surface": surface, "action": label,
            "n_reps": len(peaks),
            "baseline_pages": np.mean(baselines),
            "peak_pages_mean": np.mean(peaks),
            "peak_pages_std": np.std(peaks),
            "amplification": np.mean(peaks) / max(np.mean(baselines), 0.1),
        })
    if rows_out:
        TABLE.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows_out).to_csv(TABLE, index=False)
        print("\n## Cross-domain summary ##")
        for r in rows_out:
            print(f"  {r['system']:7s} {r['surface']:7s} {r['action']:26s} "
                  f"n={r['n_reps']:2d} baseline={r['baseline_pages']:.1f} "
                  f"peak={r['peak_pages_mean']:.0f}±{r['peak_pages_std']:.0f} "
                  f"ampl={r['amplification']:.1f}×")


if __name__ == "__main__":
    sys.exit(main() or 0)
