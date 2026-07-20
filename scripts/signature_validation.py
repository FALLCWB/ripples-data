#!/usr/bin/env python3
"""Signature validation: do reps of the same action produce similar
temporal ripple patterns?

For each rep in (D_flush, E_single_rule, F_burst,
plus Redis SET/MSET/FLUSHDB), compute a per-rep ripple time series
(novelty events bucketed in time post-action). Then compute pairwise
similarity matrix across all reps. If within-scenario pairs have
higher similarity than across-scenario pairs, the action produces a
detectable signature even when total event counts vary.

Same threshold logic as gen_crossdomain.py: signal = per-iter
n_changed_pages (Redis) or aggregated change_volume_sum (OvS);
threshold = 95th percentile of pre-action signal; per-bucket excess = sum of
above-threshold over each 5s bucket; pre-action is empty by
construction.

Outputs:
  data/processed/signature_pairwise_similarity.csv
    columns: rep_a, rep_b, scenario_a, scenario_b, same_scenario,
             cosine_similarity, dtw_distance
  data/processed/signature_summary.json
    aggregate stats: within vs across mean similarity, separation
  fig_signature.png
    similarity heatmap (rep × rep), with same-scenario blocks visible
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from regen_figs_data import per_iter_aggregates, load_rep_features, find_action_ts

ROOT = Path(__file__).resolve().parent.parent
LAB = Path(os.environ.get("OVS_SNAPSHOTS", "/tmp/lab_snapshots"))  # raw OvS not redistributed
ECHO = ROOT / "data/crossdomain"
OUT_CSV = ROOT / "data/processed/signature_pairwise_similarity.csv"
OUT_JSON = ROOT / "data/processed/signature_summary.json"
OUT_PNG = ROOT / "fig_signature.png"

BUCKET_S = 5.0
POST_WIN_S = 200.0  # OvS aftermath window; Redis short signals will pad to 0
N_BUCKETS = int(POST_WIN_S / BUCKET_S)


def is_sparse_era(d: Path) -> bool:
    # legacy capture filename, kept so first-collection snapshots still load
    prov = sorted(d.glob("provenance_*_post_attack.json"))
    if not prov:
        return False
    try:
        j = json.loads(prov[0].read_text())
        for dev in j.get("devices", {}).values():
            for e in dev:
                if e.get("category") == "STATS_RESPONSE":
                    return False
        return True
    except Exception:
        return False


def ovs_signature(rep_dir: Path) -> np.ndarray | None:
    try:
        action_ts = find_action_ts(rep_dir)
        df, _ = load_rep_features(rep_dir)
    except Exception:
        return None
    if action_ts is None:
        return None
    pre_agg = per_iter_aggregates(df[df["ts"] < action_ts], 0)
    post_agg = per_iter_aggregates(df[df["ts"] >= action_ts], 0)
    if len(pre_agg) < 10 or len(post_agg) < 5:
        return None
    threshold = float(np.percentile(pre_agg["change_volume_sum"].values, 95))
    excess = np.clip(post_agg["change_volume_sum"].values - threshold, 0, None)
    t_rel = post_agg["ts"].values - action_ts
    return bucket_signal(t_rel, excess)


def redis_signature(rep_dir: Path) -> np.ndarray | None:
    return _redis_or_dockerd_signature(rep_dir)


def dockerd_signature(rep_dir: Path) -> np.ndarray | None:
    return _redis_or_dockerd_signature(rep_dir)


def _redis_or_dockerd_signature(rep_dir: Path) -> np.ndarray | None:
    """Both Redis and Dockerd use the same features.csv format
    (ts, n_changed_pages, change_vol_bytes, entropy_mean, max_page_addr_changed)."""
    try:
        markers = json.loads((rep_dir / "markers.json").read_text())
        feat = pd.read_csv(rep_dir / "features.csv")
    except Exception:
        return None
    ats = markers["action_ts"]
    pre = feat[feat.ts < ats]
    post = feat[feat.ts >= ats]
    if len(pre) < 10 or len(post) < 5:
        return None
    threshold = float(np.percentile(pre["n_changed_pages"].values, 95))
    excess = np.clip(post["n_changed_pages"].values - threshold, 0, None)
    t_rel = post["ts"].values - ats
    return bucket_signal(t_rel, excess)


def bucket_signal(t_rel: np.ndarray, excess: np.ndarray) -> np.ndarray:
    """Bucket the excess values into N_BUCKETS bins of BUCKET_S width."""
    edges = np.arange(0.0, POST_WIN_S + BUCKET_S, BUCKET_S)
    sig = np.zeros(N_BUCKETS, dtype=float)
    mask = (t_rel >= 0) & (t_rel <= POST_WIN_S)
    idx = np.clip(np.digitize(t_rel[mask], edges) - 1, 0, N_BUCKETS - 1)
    for i, e in zip(idx, excess[mask]):
        sig[i] += e
    return sig


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def collect_signatures():
    sigs = {}  # rep_name -> (scenario, signature)
    # OvS sparse-era reps only
    for prefix in ["D_flush", "E_single_rule", "F_burst"]:
        scenario = {
            "D_flush": "OvS-Flush",
            "E_single_rule": "OvS-Single",
            "F_burst": "OvS-Burst",
        }[prefix]
        for d in sorted(LAB.glob(f"{prefix}_*")):
            if not d.is_dir():
                continue
            if not is_sparse_era(d):
                continue
            sig = ovs_signature(d)
            if sig is None:
                continue
            sigs[d.name] = (scenario, sig)
    # Redis (all reps; only one era)
    for action, scenario in [
        ("redis_set_1", "Redis-SET"),
        ("redis_mset_100", "Redis-MSET"),
        ("redis_flushdb", "Redis-FLUSHDB"),
    ]:
        for d in sorted(ECHO.glob(f"redis_{action}_rep*")):
            if not d.is_dir() or not (d / "features.csv").exists():
                continue
            sig = redis_signature(d)
            if sig is None:
                continue
            sigs[d.name] = (scenario, sig)
    # Dockerd (post-Phase-4 GC-controlled reps only — those with markers
    # produced by the bug-fixed runner; old buggy reps are skipped because
    # they lack markers.json or have inconsistent feature columns)
    for action, scenario in [
        ("docker_inspect", "Dockerd-Inspect"),
        ("docker_run_1", "Dockerd-Run1"),
        ("docker_run_10", "Dockerd-Run10"),
        ("docker_run_50", "Dockerd-Run50"),
    ]:
        for d in sorted(ECHO.glob(f"dockerd_{action}_rep*")):
            if not d.is_dir() or not (d / "features.csv").exists():
                continue
            sig = dockerd_signature(d)
            if sig is None:
                continue
            sigs[d.name] = (scenario, sig)
    return sigs


def main():
    sigs = collect_signatures()
    names = list(sigs.keys())
    n = len(names)
    print(f"Collected {n} signatures")
    by_scenario = {}
    for name, (sc, _) in sigs.items():
        by_scenario.setdefault(sc, []).append(name)
    for sc, names_in in by_scenario.items():
        print(f"  {sc}: {len(names_in)} reps")

    # Pairwise cosine
    mat = np.zeros((n, n), dtype=float)
    rows = []
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i == j:
                mat[i, j] = 1.0
                continue
            c = cosine(sigs[a][1], sigs[b][1])
            mat[i, j] = c
            if i < j:
                rows.append({
                    "rep_a": a, "rep_b": b,
                    "scenario_a": sigs[a][0], "scenario_b": sigs[b][0],
                    "same_scenario": sigs[a][0] == sigs[b][0],
                    "cosine_similarity": c,
                })
    df = pd.DataFrame(rows)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)
    print(f"Wrote {OUT_CSV}")

    # Within vs across scenario stats
    within = df[df.same_scenario]["cosine_similarity"].values
    across = df[~df.same_scenario]["cosine_similarity"].values
    summary = {
        "n_signatures": n,
        "n_within_pairs": int(len(within)),
        "n_across_pairs": int(len(across)),
        "within_mean": float(within.mean()) if len(within) else None,
        "within_std": float(within.std()) if len(within) else None,
        "within_median": float(np.median(within)) if len(within) else None,
        "across_mean": float(across.mean()) if len(across) else None,
        "across_std": float(across.std()) if len(across) else None,
        "across_median": float(np.median(across)) if len(across) else None,
        "separation_ratio": (float(within.mean() / across.mean())
                             if len(across) and across.mean() > 0 else None),
    }
    # Per-scenario within-mean
    per_scenario_within = {}
    for sc, names_in in by_scenario.items():
        idx = [names.index(n) for n in names_in]
        sub = mat[np.ix_(idx, idx)]
        mask = ~np.eye(len(idx), dtype=bool)
        if mask.sum() == 0:
            continue
        per_scenario_within[sc] = {
            "n_reps": len(idx),
            "within_mean": float(sub[mask].mean()),
            "within_std": float(sub[mask].std()),
        }
    summary["per_scenario_within"] = per_scenario_within
    OUT_JSON.write_text(json.dumps(summary, indent=2))
    print(f"Wrote {OUT_JSON}")
    print(f"\n=== Summary ===")
    print(f"  within-scenario mean cosine: {summary['within_mean']:.3f}")
    print(f"  across-scenario mean cosine: {summary['across_mean']:.3f}")
    print(f"  separation ratio: {summary['separation_ratio']:.2f}x")
    print(f"\n  Per-scenario within-mean:")
    for sc, info in per_scenario_within.items():
        print(f"    {sc:18s} n={info['n_reps']:3d}  within-mean={info['within_mean']:.3f} ± {info['within_std']:.3f}")

    # Heatmap
    order = sorted(range(n), key=lambda i: (sigs[names[i]][0], names[i]))
    ordered_names = [names[i] for i in order]
    ordered_mat = mat[np.ix_(order, order)]
    ordered_scenarios = [sigs[ordered_names[i]][0] for i in range(n)]

    fig, ax = plt.subplots(figsize=(11, 9))
    im = ax.imshow(ordered_mat, cmap="viridis", vmin=0, vmax=1, aspect="equal")
    fig.colorbar(im, ax=ax, label="Cosine similarity")
    # Draw scenario boundaries
    boundaries = []
    prev = None
    for i, sc in enumerate(ordered_scenarios):
        if sc != prev:
            boundaries.append(i)
            prev = sc
    boundaries.append(n)
    for b in boundaries[1:-1]:
        ax.axhline(b - 0.5, color="white", lw=1.0)
        ax.axvline(b - 0.5, color="white", lw=1.0)
    # Label each scenario block at its center
    centers = [(boundaries[i] + boundaries[i + 1]) / 2 - 0.5
               for i in range(len(boundaries) - 1)]
    labels = [ordered_scenarios[int(boundaries[i])]
              for i in range(len(boundaries) - 1)]
    ax.set_xticks(centers)
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_yticks(centers)
    ax.set_yticklabels(labels)
    ax.set_title(
        f"Pairwise ripple-signature similarity (cosine)\n"
        f"within-scenario mean = {summary['within_mean']:.2f},  "
        f"across-scenario mean = {summary['across_mean']:.2f}  "
        f"(separation {summary['separation_ratio']:.2f}×)"
    )
    plt.tight_layout()
    plt.savefig(OUT_PNG, dpi=200)
    plt.close()
    print(f"Wrote {OUT_PNG}")


if __name__ == "__main__":
    main()
