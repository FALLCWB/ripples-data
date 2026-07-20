#!/usr/bin/env python3
"""Regenerate the OvS figure/table CSVs for the paper from the released processed
outputs, under the manuscript's definitive labeler (Induced-cascade before
Direct-anchor, Algorithm 1) and the p95 warmup threshold.

Inputs (all under data/processed/, no raw snapshots needed; the 1.3 GB raw
recollection is too large to host online):
  - labels_corrected_{sparse,rich}_W2.0_C5.0_D300.json : per-rep six-category
    counts/rates from the Induced-first labeler (labeler_v2), used for the
    scenario decomposition (Table 3) and Fig 2(a) magnitude.
  - ovs_recollection_aggregates/<rep>.csv : per-0.5 s-iteration aggregates
    (change_volume_sum etc.) with markers and action_ts, used for the temporal
    profile (Fig 5) and per-iteration feature distributions (Fig 6).

Outputs (data/processed/): scenario_decomposition.csv,
fig2_sparse_attack_cascade_per_rep.csv, fig5_temporal_signal.csv,
fig6_feature_distributions.csv.
"""
import csv, json, statistics as st
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
AGG = PROC / "ovs_recollection_aggregates"
SIG = "change_volume_sum"
AFTERMATH_S = 300.0
PRE_Q = 95
SCEN = {"A_idle": "Idle", "B_flow_install": "Rule installation",
        "C_ping_sustained": "Sustained traffic", "D_flush": "Flow-table flush",
        "E_single_rule": "Single-rule insertion", "F_burst": "Multi-rule burst"}
CATS = ["Direct-anchor", "Reactive-cascade", "Induced-cascade",
        "Periodic-gap", "Endogenous-unexplained", "Indeterminate"]
SURF = {"E_single_rule": 1, "F_burst": 21, "D_flush": 200}
FIG_KEY = {"D_flush": "D_attack_flush", "E_single_rule": "E_attack_single_rule",
           "F_burst": "F_attack_burst"}


def load_labels(name):
    return json.load(open(PROC / name))["results"]


def load_agg(rep_stem):
    df = pd.read_csv(AGG / f"{rep_stem}.csv")
    return df


def p95(warm):
    return float(np.percentile(warm[SIG], PRE_Q))


def warm_test(df):
    ws, ca, ts = df["warmup_start_ts"].iloc[0], df["controller_attached_ts"].iloc[0], df["test_phase_start_ts"].iloc[0]
    return df[(df["ts"] >= ws) & (df["ts"] < ca)], df[df["ts"] >= ts]


# ---------------------------------------------------------------- Table 3 + Fig 2
def build_decomposition_and_fig2():
    def per_scen(results):
        agg = defaultdict(list)
        for r in results:
            if r.get("excluded") or r["scenario"] not in SCEN:
                continue
            agg[r["scenario"]].append(r)
        return agg

    sp = per_scen(load_labels("labels_corrected_sparse_W2.0_C5.0_D300.json"))
    ri = per_scen(load_labels("labels_corrected_rich_W2.0_C5.0_D300.json"))

    def row(sc, reps, audit):
        d = {"scenario": SCEN[sc], "audit": audit, "reps": len(reps)}
        for c in CATS:
            d[c] = round(st.mean([r["rates_per_h"].get(c, 0.0) for r in reps]))
        return d

    with open(PROC / "scenario_decomposition.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario", "audit", "reps"] + CATS)
        for sc in ["A_idle", "B_flow_install", "C_ping_sustained"]:
            r = row(sc, sp[sc], "sparse"); w.writerow([r["scenario"], r["audit"], r["reps"]] + [r[c] for c in CATS])
        r = row("D_flush", ri["D_flush"], "rich"); w.writerow([r["scenario"], "rich", r["reps"]] + [r[c] for c in CATS])
        for sc in ["D_flush", "E_single_rule", "F_burst"]:
            r = row(sc, sp[sc], "sparse"); w.writerow([r["scenario"], r["audit"], r["reps"]] + [r[c] for c in CATS])

    cols = ["scenario", "rep_id", "direct_anchor", "reactive_cascade", "induced_cascade",
            "periodic_gap", "endogenous_unexplained", "indeterminate", "duration_s",
            "induced_cascade_count", "per_hour_rate", "threshold_used", "n_test_iters", "n_ext"]
    rows = []
    for r in load_labels("labels_corrected_sparse_W2.0_C5.0_D300.json"):
        if r.get("excluded") or r["scenario"] not in FIG_KEY:
            continue
        c = r["counts"]; ic = c.get("Induced-cascade", 0)
        rows.append({"scenario": FIG_KEY[r["scenario"]], "rep_id": r["rep"],
                     "direct_anchor": c.get("Direct-anchor", 0), "reactive_cascade": c.get("Reactive-cascade", 0),
                     "induced_cascade": ic, "periodic_gap": c.get("Periodic-gap", 0),
                     "endogenous_unexplained": c.get("Endogenous-unexplained", 0),
                     "indeterminate": c.get("Indeterminate", 0), "duration_s": round(r["dur_h"] * 3600, 2),
                     "induced_cascade_count": ic, "per_hour_rate": round(ic / r["dur_h"], 1),
                     "threshold_used": r.get("threshold", 0), "n_test_iters": r.get("n_iters_test", 0),
                     "n_ext": r.get("n_events", 0)})
    order = {"E_attack_single_rule": 0, "F_attack_burst": 1, "D_attack_flush": 2}
    rows.sort(key=lambda x: (order[x["scenario"]], x["rep_id"]))
    with open(PROC / "fig2_sparse_attack_cascade_per_rep.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)
    means = {k: st.mean([x["per_hour_rate"] for x in rows if x["scenario"] == FIG_KEY[k]]) for k in SURF}
    print(f"decomposition + fig2: means single/burst/flush = "
          f"{means['E_single_rule']:.0f}/{means['F_burst']:.0f}/{means['D_flush']:.0f}")


# --------------------------------------------------------------------- Fig 5 / Fig 6
def representative_flush():
    best = []
    for f in sorted(AGG.glob("D_flush_rep*.csv")):
        df = load_agg(f.stem)
        warm, test = warm_test(df)
        if len(warm) < 10:
            continue
        at = float(df["action_ts"].iloc[0]); thr = p95(warm)
        post = test[(test["ts"] >= at) & (test["ts"] <= at + 200)]
        best.append(((post[SIG] - thr).clip(lower=0).sum(), f.stem))
    best.sort(key=lambda x: x[0])
    return best[len(best) // 2][1]  # median excursion, deterministic


def build_fig5_fig6():
    stem = representative_flush()
    df = load_agg(stem)
    warm, test = warm_test(df)
    at = float(df["action_ts"].iloc[0]); thr = p95(warm)

    win = test[(test["ts"] >= at - 30) & (test["ts"] <= at + 200)].copy()
    win["t_rel_s"] = win["ts"] - at
    f5 = win[["t_rel_s", SIG]].rename(columns={SIG: "signal"}).sort_values("t_rel_s")
    f5["threshold"] = thr
    f5.to_csv(PROC / "fig5_temporal_signal.csv", index=False)

    ripple = test[(test[SIG] > thr) & (test["ts"] >= at) & (test["ts"] <= at + AFTERMATH_S)].copy()
    idle_frames = [warm]
    for f in sorted(AGG.glob("A_idle_rep*.csv"))[:3]:
        di = load_agg(f.stem)
        idle_frames.append(di[di["ts"] >= di["test_phase_start_ts"].iloc[0]])
    baseline = pd.concat(idle_frames, ignore_index=True)
    feat_map = {"Active pages": "n_active", "Volume sum": SIG,
                "High-region pages": "region_high", "Entropy (changed)": "entropy_changed_mean"}
    rows = []
    for label, col in feat_map.items():
        for kind, src in (("baseline", baseline), ("ripple", ripple)):
            for v in src[col].fillna(0).values:
                rows.append({"feature": label, "kind": kind, "value": float(v)})
    pd.DataFrame(rows).to_csv(PROC / "fig6_feature_distributions.csv", index=False)
    pre_ex = float((win[win["ts"] < at][SIG] - thr).clip(lower=0).sum())
    post_ex = float((win[win["ts"] >= at][SIG] - thr).clip(lower=0).sum())
    print(f"fig5 (rep {stem}): ratio={post_ex/max(pre_ex,1):.1f}x  fig6: ripple n={len(ripple)} baseline n={len(baseline)}")


if __name__ == "__main__":
    build_decomposition_and_fig2()
    build_fig5_fig6()
    print("Done.")
