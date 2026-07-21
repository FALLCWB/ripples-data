#!/usr/bin/env python3
"""Signature reproducibility restricted to the conditions that produce a cascade.

The corpus-wide separation is computed over every repetition in the three
daemons, including conditions that produce only a first-order write (Redis SET
and MSET, the Dockerd readback) and the OvS single-rule scenario, for which no
action-attributable step is resolved. Those traces can reproduce without there
being a cascade to reproduce, so the primary statistic here is restricted to the
conditions independently classified as cascade-present.

Two outputs:
  1. within/across cosine over cascade-present conditions only, globally and
     blocked within daemon, with the repetition-level label-permutation test and
     a repetition-level bootstrap (same resampling as signature_replevel_perm).
  2. a no-action control for OvS: the same vector construction over a window of
     the same runs that contains no action, so that the daemon's post-action
     trace repeatability can be compared against its own background.

Output: data/processed/signature_cascade_present.json

Usage: python3 scripts/signature_cascade_present.py [--n-perm 10000]
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
PAIRS = PROC / "signature_pairwise_similarity.csv"
AGG = PROC / "ovs_recollection_aggregates"
SIG = "change_volume_sum"
PRE_Q = 95
SEED = 42
# Classified cascade-present in the presence table; the rest of the corpus is
# splash-only (Redis SET/MSET, Dockerd readback) or unresolved (OvS single rule,
# Dockerd single container).
CASCADE_PRESENT = ("OvS-Flush", "OvS-Burst", "Redis-FLUSHDB", "Dockerd-Run10", "Dockerd-Run50")
DAEMON_OF = {"OvS-Flush": "OvS", "OvS-Burst": "OvS", "OvS-Single": "OvS",
             "Redis-SET": "Redis", "Redis-MSET": "Redis", "Redis-FLUSHDB": "Redis",
             "Dockerd-Inspect": "Dockerd", "Dockerd-Run1": "Dockerd",
             "Dockerd-Run10": "Dockerd", "Dockerd-Run50": "Dockerd"}


def separation(df):
    same = df[df["same_scenario"].astype(str).str.lower() == "true"]
    diff = df[df["same_scenario"].astype(str).str.lower() != "true"]
    if same.empty or diff.empty:
        return None
    w, a = float(same["cosine_similarity"].mean()), float(diff["cosine_similarity"].mean())
    return {"within": round(w, 4), "across": round(a, 4), "separation": round(w / a, 3),
            "n_pairs_within": int(len(same)), "n_pairs_across": int(len(diff))}


def perm_test(df, rng, n_perm):
    """Permute repetition-to-scenario labels, not pairs."""
    reps = sorted(set(df["rep_a"]) | set(df["rep_b"]))
    lab = {}
    for _, r in df.iterrows():
        lab[r["rep_a"]] = r["scenario_a"]
        lab[r["rep_b"]] = r["scenario_b"]
    labels = np.array([lab[r] for r in reps])
    idx = {r: i for i, r in enumerate(reps)}
    a = df["rep_a"].map(idx).to_numpy()
    b = df["rep_b"].map(idx).to_numpy()
    v = df["cosine_similarity"].to_numpy()

    def stat(lb):
        same = lb[a] == lb[b]
        if same.all() or (~same).all():
            return np.nan
        return v[same].mean() / v[~same].mean()

    obs = stat(labels)
    hits = 0
    for _ in range(n_perm):
        if stat(rng.permutation(labels)) >= obs:
            hits += 1
    return round(float(obs), 3), (hits + 1) / (n_perm + 1)


def boot_ci(df, rng, n_boot=2000):
    reps = sorted(set(df["rep_a"]) | set(df["rep_b"]))
    idx = {r: i for i, r in enumerate(reps)}
    a = df["rep_a"].map(idx).to_numpy()
    b = df["rep_b"].map(idx).to_numpy()
    v = df["cosine_similarity"].to_numpy()
    same = (df["scenario_a"] == df["scenario_b"]).to_numpy()
    out = []
    for _ in range(n_boot):
        mult = np.bincount(rng.integers(0, len(reps), len(reps)), minlength=len(reps))
        w = mult[a] * mult[b]
        if (w * same).sum() == 0 or (w * ~same).sum() == 0:
            continue
        wm = (v * w * same).sum() / (w * same).sum()
        am = (v * w * ~same).sum() / (w * ~same).sum()
        if am:
            out.append(wm / am)
    return [round(float(np.percentile(out, 2.5)), 3), round(float(np.percentile(out, 97.5)), 3)]


def ovs_window_signature(lo_offset, hi_offset, bucket=5.0):
    """5 s-bucketed above-threshold excess vectors over a window of the OvS runs."""
    vecs = {}
    for path in sorted(AGG.glob("*.csv")):
        scen = next((s for s in ("D_flush", "E_single_rule", "F_burst")
                     if path.stem.startswith(s)), None)
        if scen is None:
            continue
        df = pd.read_csv(path)
        ws, ca = df["warmup_start_ts"].iloc[0], df["controller_attached_ts"].iloc[0]
        warm = df[(df["ts"] >= ws) & (df["ts"] < ca)]
        a = float(df["action_ts"].iloc[0])
        if len(warm) < 10 or not np.isfinite(a):
            continue
        thr = float(np.percentile(warm[SIG], PRE_Q))
        n = int((hi_offset - lo_offset) / bucket)
        v = np.zeros(n)
        for k in range(n):
            lo = a + lo_offset + k * bucket
            w = df[(df["ts"] >= lo) & (df["ts"] < lo + bucket)]
            s = w[w[SIG] > thr]
            v[k] = float((s[SIG] - thr).sum())
        if v.sum() > 0:
            vecs[path.stem] = (scen, v / np.linalg.norm(v))
    names = list(vecs)
    within, across = [], []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            si, vi = vecs[names[i]]
            sj, vj = vecs[names[j]]
            (within if si == sj else across).append(float(vi @ vj))
    if not within or not across:
        return None
    return {"n_reps": len(names), "within": round(float(np.mean(within)), 3),
            "across": round(float(np.mean(across)), 3),
            "separation": round(float(np.mean(within) / np.mean(across)), 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-perm", type=int, default=10000)
    args = ap.parse_args()
    rng = np.random.default_rng(SEED)

    df = pd.read_csv(PAIRS)
    present = df[df["scenario_a"].isin(CASCADE_PRESENT) & df["scenario_b"].isin(CASCADE_PRESENT)]
    out = {"params": {"cascade_present_conditions": list(CASCADE_PRESENT),
                      "n_perm": args.n_perm, "seed": SEED},
           "full_corpus": separation(df),
           "cascade_present": separation(present)}

    ratio, p = perm_test(present, rng, args.n_perm)
    out["cascade_present"]["permutation_p"] = p
    out["cascade_present"]["bootstrap_ci"] = boot_ci(present, rng)

    blocked = present[present["scenario_a"].map(DAEMON_OF) == present["scenario_b"].map(DAEMON_OF)]
    out["cascade_present_within_daemon"] = separation(blocked)
    for daemon in ("OvS", "Dockerd", "Redis"):
        sub = blocked[blocked["scenario_a"].map(DAEMON_OF) == daemon]
        s = separation(sub) if not sub.empty else None
        out.setdefault("per_daemon", {})[daemon] = s or "not estimable (one cascade-present action)"

    out["ovs_no_action_control"] = {
        "post_action_window": ovs_window_signature(0.0, 150.0),
        "no_action_window": ovs_window_signature(-300.0, -150.0),
        "note": ("Same vector construction on a window of the same runs that contains no "
                 "action. If the no-action window separates as well as the aftermath, the "
                 "daemon's trace repeatability is not action-attributable."),
    }

    (PROC / "signature_cascade_present.json").write_text(json.dumps(out, indent=2))
    cp = out["cascade_present"]
    print(f"full corpus        within {out['full_corpus']['within']} across {out['full_corpus']['across']} "
          f"= {out['full_corpus']['separation']}x")
    print(f"cascade-present    within {cp['within']} across {cp['across']} = {cp['separation']}x "
          f"(perm p = {cp['permutation_p']}, CI {cp['bootstrap_ci']})")
    wd = out["cascade_present_within_daemon"]
    print(f"within daemon      within {wd['within']} across {wd['across']} = {wd['separation']}x")
    for d, s in out["per_daemon"].items():
        print(f"  {d:8s} {s if isinstance(s, str) else str(s['separation']) + 'x'}")
    c = out["ovs_no_action_control"]
    print(f"OvS post-action window {c['post_action_window']}")
    print(f"OvS no-action  window {c['no_action_window']}")


if __name__ == "__main__":
    main()
