#!/usr/bin/env python3
"""Revision figures for the action-ripples paper (resubmission).

  fig_overhead.png   - observer cost vs heap size + target-throughput impact (R2#7/R4#4)
  fig_gcdefault.png  - surface/magnitude dissociation holds under default GC (R2#10/R4#5)

Same matplotlib style as gen_figures.py (serif, dpi 300, PALETTE) so the new
figures are visually consistent with fig2/fig5/fig6. Overhead values are the
n=5 aggregates from exp_overhead/results/*_summary.txt; the default-GC arm is
computed from the collected reps and the preserved GOGC=off arm from
crossdomain_summary.csv.
"""
import csv
import glob
import json
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

OUT = Path(__file__).parent
DATA = OUT / "data" / "processed"
GCDIR = Path(os.path.expanduser("~/research/ripples-recollection/gcdefault"))

plt.rcParams.update({
    "font.family": "serif", "font.size": 15, "axes.labelsize": 16,
    "axes.titlesize": 17, "xtick.labelsize": 14, "ytick.labelsize": 14,
    "legend.fontsize": 14, "figure.dpi": 100, "savefig.dpi": 300,
    "savefig.bbox": "tight", "axes.grid": True, "grid.alpha": 0.3,
    "axes.spines.top": False, "axes.spines.right": False,
    "lines.linewidth": 2.0, "lines.markersize": 9,
})
PALETTE = {"anchored": "#1b7837", "reactive": "#7fbf7b", "induced": "#762a83",
           "periodic": "#fdb863", "endogenous": "#b35806", "neutral": "#2c7bb6",
           "baseline": "#a6cee3"}


# --- overhead aggregates (n=5), from exp_overhead/results/*_summary.txt --------
OVH = {
    "Redis\n(39 MB heap)":   {"heap": 39,  "cpu": 44.6, "rss": 81.5,
                              "lat_p50": 221, "lat_p95": 242},
    "Dockerd\n(140 MB heap)": {"heap": 140, "cpu": 100.3, "rss": 206.1,
                              "lat_p50": 810, "lat_p95": 844},
}
# target throughput with vs without observer (mean, sd)
THRU = [  # (label, with_mean, with_sd, without_mean, without_sd, higher_is_better)
    ("Redis\nSET",  34068, 2201, 34698, 576,  True),
    ("Redis\nGET",  34059, 1493, 32618, 1731, True),
    ("Dockerd\n20x run", 192.6, 31.2, 214.2, 71.2, False),  # wall seconds -> invert
]


def fig_overhead():
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5.5))
    labels = list(OVH.keys())
    x = np.arange(len(labels))
    p50 = np.array([OVH[k]["lat_p50"] for k in labels])
    err = np.array([OVH[k]["lat_p95"] - OVH[k]["lat_p50"] for k in labels])

    # Panel (a): dump latency (cost) with CPU% and RSS annotated; cost scales with heap.
    bars = ax1.bar(x, p50, yerr=err, capsize=8, width=0.55,
                   color=PALETTE["neutral"], edgecolor="black", linewidth=0.6,
                   error_kw={"elinewidth": 1.5, "ecolor": "black"},
                   label="dump latency p50 (p95 cap)")
    for i, k in enumerate(labels):
        ax1.annotate(f"CPU {OVH[k]['cpu']:.0f}% of a core\nRSS {OVH[k]['rss']:.0f} MB",
                     xy=(i, p50[i] + err[i] + 55), ha="center", va="bottom",
                     fontsize=11, fontweight="bold")
    ax1.set_xticks(x); ax1.set_xticklabels(labels)
    ax1.set_ylabel("Per-dump latency (ms)")
    ax1.set_ylim(0, 1150)
    ax1.set_title("Observer cost scales with heap size")
    ax1.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16),
               ncol=1, framealpha=0.95)
    ax1.text(0.02, 0.97, "(a)", transform=ax1.transAxes, fontsize=18,
             fontweight="bold", va="top", ha="left")

    # Panel (b): relative throughput (with/without observer); 1.0 = no impact.
    rlab, rratio, rerr = [], [], []
    for lab, wm, ws, om, os_, hib in THRU:
        # throughput ratio with/without; for wall time (lower better) invert.
        r = (wm / om) if hib else (om / wm)
        cv = np.sqrt((ws / wm) ** 2 + (os_ / om) ** 2)
        rlab.append(lab); rratio.append(r); rerr.append(r * cv)
    xr = np.arange(len(rlab))
    ax2.bar(xr, rratio, yerr=rerr, capsize=8, width=0.55,
            color=PALETTE["anchored"], edgecolor="black", linewidth=0.6,
            error_kw={"elinewidth": 1.5, "ecolor": "black"})
    ax2.axhline(1.0, linestyle="--", color="gray", lw=1.2, alpha=0.8,
                label="no impact (1.0)")
    for i, (r, e) in enumerate(zip(rratio, rerr)):
        ax2.annotate(f"{r:.2f}", xy=(i, r + e + 0.02), ha="center", va="bottom",
                     fontsize=11, fontweight="bold")
    ax2.set_xticks(xr); ax2.set_xticklabels(rlab)
    ax2.set_ylabel("Relative throughput\n(with observer / without)")
    ax2.set_ylim(0, 1.7)
    ax2.set_title("No measurable impact on the target")
    ax2.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16),
               ncol=1, framealpha=0.95)
    ax2.text(0.02, 0.97, "(b)", transform=ax2.transAxes, fontsize=18,
             fontweight="bold", va="top", ha="left")
    plt.tight_layout()
    plt.savefig(OUT / "fig_overhead.png")
    plt.close()
    print("fig_overhead done")


def _amp_gcdefault(action):
    """Peak/baseline amplification per rep for a GOGC=100 dockerd action."""
    out = []
    for d in sorted(glob.glob(str(GCDIR / f"dockerd_gcdefault_{action}_rep*"))):
        mj = Path(d) / "markers.json"
        if not mj.exists():
            continue
        a = json.loads(mj.read_text())["action_ts"]
        f = glob.glob(f"{d}/features*.csv")
        if not f:
            continue
        warm, post = [], []
        for r in csv.reader(open(f[0])):
            if not r or not r[0].replace(".", "", 1).isdigit():
                continue
            ts, pg = float(r[0]), float(r[1])
            (post if ts >= a else warm).append(pg)
        if len(warm) >= 5 and post:
            out.append(max(post) / (np.mean(warm) or 1e-9))
    return out


def fig_gcdefault():
    # preserved GOGC=off amplification from crossdomain_summary.csv
    off = {}
    for row in csv.DictReader(open(DATA / "crossdomain_summary.csv")):
        if row["system"] == "Dockerd":
            off[row["action"]] = float(row["amplification"])
    off_vals = [off.get("docker version (readback)", off.get("docker version", np.nan)),
                off["1 container"], off["10 containers"]]
    # default-GC amplification computed from the reps (mean per action)
    dflt_vals = [np.mean(_amp_gcdefault(a)) for a in
                 ["docker_inspect", "docker_run_1", "docker_run_10"]]

    labels = ["readback\n(control)", "1 container", "10 containers"]
    x = np.arange(len(labels))
    width = 0.38
    fig, ax = plt.subplots(figsize=(10.5, 5.5))
    b1 = ax.bar(x - width / 2, off_vals, width, label="GOGC=off (tuned, original)",
                color=PALETTE["neutral"], edgecolor="black", linewidth=0.6)
    b2 = ax.bar(x + width / 2, dflt_vals, width, label="GOGC=100 (default GC)",
                color=PALETTE["induced"], edgecolor="black", linewidth=0.6)
    ax.set_yscale("log")
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.set_ylabel("Peak / baseline amplification\n(log scale)")
    ax.set_xlabel("Action surface (containers spawned)")
    ax.set_title("Magnitude does not track surface, under default GC too")
    for bars, vals in [(b1, off_vals), (b2, dflt_vals)]:
        for bar, v in zip(bars, vals):
            ax.annotate(f"{v:.1f}x", xy=(bar.get_x() + bar.get_width() / 2, v * 1.05),
                        ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax.set_ylim(1, 200)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.20),
              ncol=2, framealpha=0.95)
    ax.annotate("default-GC dissociation:\nSpearman $\\rho$(surface, amplif.) = 0.09\n"
                "95% CI [$-0.24$, $0.41$] (contains 0)",
                xy=(0.03, 0.97), xycoords="axes fraction", ha="left", va="top",
                fontsize=11, style="italic",
                bbox=dict(boxstyle="round,pad=0.35", facecolor="white",
                          edgecolor="grey", alpha=0.95))
    plt.tight_layout()
    plt.savefig(OUT / "fig_gcdefault.png")
    plt.close()
    print(f"fig_gcdefault done (off={[round(v,1) for v in off_vals]}, "
          f"default={[round(v,1) for v in dflt_vals]})")


def fig_robustness():
    # within-scenario signature cosine (E2 method: p95 pre-action, WIN=200, BUCKET=5)
    # measured from ripples-recollection/r46_server (redis6/Debian) and r46_local
    # (redis7/this-PC); preserved column from signature_summary.json (redis7/Alpine).
    actions = ["SET\n(1 key)", "MSET\n(100 keys)", "FLUSHDB"]
    within = {
        "Redis 7 / Alpine (paper)":   [0.862, 0.999, 0.959],
        "Redis 6 / Debian (server)":  [0.856, 0.848, 0.754],
        "Redis 7 / Alpine (2nd host)": [0.811, 0.931, 0.992],
    }
    colors = [PALETTE["neutral"], PALETTE["induced"], PALETTE["anchored"]]
    x = np.arange(len(actions)); width = 0.26
    fig, ax = plt.subplots(figsize=(11, 5.5))
    for k, (label, vals) in enumerate(within.items()):
        off = (k - 1) * width
        b = ax.bar(x + off, vals, width, label=label, color=colors[k],
                   edgecolor="black", linewidth=0.5)
        for bar, v in zip(b, vals):
            ax.annotate(f"{v:.2f}", xy=(bar.get_x() + bar.get_width() / 2, v + 0.012),
                        ha="center", va="bottom", fontsize=9)
    ax.set_xticks(x); ax.set_xticklabels(actions)
    ax.set_ylabel("Within-scenario signature similarity\n(mean pairwise cosine)")
    ax.set_ylim(0, 1.15)
    ax.set_yticks([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.set_title("Per-action signature reproduces across version, OS, and hardware")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=3, framealpha=0.95)
    plt.tight_layout()
    plt.savefig(OUT / "fig_robustness.png")
    plt.close()
    print("fig_robustness done")


if __name__ == "__main__":
    fig_overhead()
    fig_gcdefault()
    fig_robustness()
    print("Revision figures written to", OUT)
