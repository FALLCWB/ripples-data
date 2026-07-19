#!/usr/bin/env python3
"""Compare different threshold treatments on the same data corpus.

Goal: pick the single treatment that best isolates exogenous (induced
cascade) from endogenous (internal unexplained) activity.

For each treatment we report, per OvS scenario:
  - n_reps
  - recall: fraction of induced-event reps where any post-action iteration
    is flagged (only applies to D/E/F scenarios)
  - induced_per_hour: mean per-hour Induced-cascade rate in induced reps
  - endogenous_per_hour: mean per-hour Endogenous-unexplained rate
  - SNR: induced/(endogenous of idle/rich + 1) — higher is better isolation
  - lat_mean: mean first-flag latency in seconds for induced reps

Treatments:
  T1: max(pre)                — current production
  T2: p99(pre)
  T3: p95(pre)
  T4: z-score, mean(pre)+3*std(pre)
  T5: z-score, mean(pre)+5*std(pre)
  T6: rolling median + 2*MAD over pre-action

Treatments are evaluated against scenario_decomposition's category labels
(Direct/Reactive/Induced/Periodic/Endogenous/Indeterminate) computed by
re-running classify() on the iterations flagged by each treatment.
"""
import os
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from regen_figs_data import (  # noqa: E402
    per_iter_aggregates, load_rep_features, find_action_ts,
    load_audit, predict_cadence, classify, is_sparse_era,
    SIGNAL_COL, LLDP_PERIOD, STATS_PERIOD,
)

SNAPSHOTS = Path(os.environ.get("OVS_SNAPSHOTS", "/tmp/lab_snapshots"))
OUT_CSV = Path(__file__).parent.parent / "data/processed/threshold_comparison.csv"


def t1_max(pre_signal: np.ndarray) -> float:
    return float(np.max(pre_signal)) if len(pre_signal) else float("inf")


def t2_p99(pre_signal: np.ndarray) -> float:
    return float(np.percentile(pre_signal, 99)) if len(pre_signal) else float("inf")


def t3_p95(pre_signal: np.ndarray) -> float:
    return float(np.percentile(pre_signal, 95)) if len(pre_signal) else float("inf")


def t4_z3(pre_signal: np.ndarray) -> float:
    if len(pre_signal) == 0:
        return float("inf")
    return float(np.mean(pre_signal) + 3 * np.std(pre_signal))


def t5_z5(pre_signal: np.ndarray) -> float:
    if len(pre_signal) == 0:
        return float("inf")
    return float(np.mean(pre_signal) + 5 * np.std(pre_signal))


def t6_mad(pre_signal: np.ndarray) -> float:
    """Median + 5 * MAD (robust to outliers; MAD ≈ 0.6745 * std for normal)."""
    if len(pre_signal) == 0:
        return float("inf")
    med = float(np.median(pre_signal))
    mad = float(np.median(np.abs(pre_signal - med)))
    return med + 5.0 * mad


TREATMENTS = {
    "T1_max":  t1_max,
    "T2_p99":  t2_p99,
    "T3_p95":  t3_p95,
    "T4_z3":   t4_z3,
    "T5_z5":   t5_z5,
    "T6_mad5": t6_mad,
}


def analyze_rep_under_treatment(sd: Path, threshold_fn) -> dict | None:
    try:
        markers = json.loads((sd / "markers.json").read_text())
        p1_s = markers["warmup_start_ts"]
        p1_e = markers["controller_attached_ts"]
        t_s = markers["test_phase_start_ts"]
    except Exception:
        return None
    try:
        df, max_page = load_rep_features(sd)
    except Exception:
        return None
    agg_p1 = per_iter_aggregates(df[(df["ts"] >= p1_s) & (df["ts"] < p1_e)], max_page)
    df_test = df[df["ts"] >= t_s]
    agg_test = per_iter_aggregates(df_test, max_page)
    if len(agg_p1) < 10 or len(agg_test) < 10:
        return None
    action_ts = find_action_ts(sd)
    threshold = threshold_fn(agg_p1[SIGNAL_COL].values)
    ext_mask = agg_test[SIGNAL_COL].values > threshold
    ext_ts = agg_test["ts"][ext_mask].values

    all_audit, by_cat = load_audit(sd)
    t_e = df_test["ts"].max()
    lldp_a = by_cat.get("LLDP_REFRESH", by_cat.get("LINK_EVENT", np.array([])))
    stats_a = by_cat.get("STATS_RESPONSE", by_cat.get("PORT_EVENT", np.array([])))
    lldp_ticks = predict_cadence(lldp_a, LLDP_PERIOD, t_s, t_e)
    stats_ticks = predict_cadence(stats_a, STATS_PERIOD, t_s, t_e)

    counts = defaultdict(int)
    for ts in ext_ts:
        counts[classify(ts, all_audit, lldp_ticks, stats_ticks, action_ts)] += 1

    dur_s = float(t_e - t_s)
    first_lat = float("inf")
    if action_ts is not None:
        post = ext_ts[ext_ts >= action_ts]
        if len(post) > 0:
            first_lat = float(post[0] - action_ts)

    return {
        "scenario": sd.name.split("_rep")[0],
        "rep_id": sd.name,
        "audit_era": "sparse" if is_sparse_era(sd) else "rich",
        "n_test_iters": int(len(agg_test)),
        "n_flagged": int(ext_mask.sum()),
        "threshold": threshold,
        "direct_anchor": int(counts.get("Direct-anchor", 0)),
        "reactive_cascade": int(counts.get("Reactive-cascade", 0)),
        "induced_cascade": int(counts.get("Induced-cascade", 0)),
        "periodic_gap": int(counts.get("Periodic-gap", 0)),
        "endogenous_unexplained": int(counts.get("Endogenous-unexplained", 0)),
        "indeterminate": int(counts.get("Indeterminate", 0)),
        "duration_s": dur_s,
        "first_lat_s": first_lat,
    }


def main():
    # Collect reps we care about
    prefixes = ["A_idle", "B_flow_install", "C_ping_sustained",
                "D_attack_flush", "E_attack_single_rule", "F_attack_burst"]
    rep_dirs = [d for d in sorted(SNAPSHOTS.iterdir())
                if d.is_dir() and any(d.name.startswith(p) for p in prefixes)]
    print(f"Found {len(rep_dirs)} rep dirs")

    all_rows = []
    for ti, (name, fn) in enumerate(TREATMENTS.items()):
        print(f"\n[{ti+1}/{len(TREATMENTS)}] {name}")
        for sd in rep_dirs:
            r = analyze_rep_under_treatment(sd, fn)
            if r is None:
                continue
            r["treatment"] = name
            all_rows.append(r)
    df = pd.DataFrame(all_rows)
    df.to_csv(OUT_CSV, index=False)
    print(f"\nWrote {OUT_CSV} ({len(df)} rows)")

    # Aggregate summary
    print("\n=== Per-treatment summary ===")
    print(f"{'treatment':10s} {'scenario':22s} {'era':6s} {'n':3s} "
          f"{'recall':8s} {'induced/h':12s} {'endog/h':10s} {'lat':6s}")
    for treatment in TREATMENTS:
        sub_t = df[df.treatment == treatment]
        for (scenario, era), grp in sub_t.groupby(["scenario", "audit_era"]):
            n = len(grp)
            hours = grp["duration_s"].sum() / 3600.0
            induced_h = grp["induced_cascade"].sum() / hours if hours else 0
            endo_h = grp["endogenous_unexplained"].sum() / hours if hours else 0
            recall = (grp["induced_cascade"] > 0).sum() / n if scenario.startswith(("D_", "E_", "F_")) else float("nan")
            finite_lat = grp["first_lat_s"][grp["first_lat_s"] < float("inf")]
            lat = finite_lat.mean() if len(finite_lat) > 0 else float("nan")
            recall_str = f"{recall:.0%}" if not np.isnan(recall) else "--"
            lat_str = f"{lat:.2f}s" if not np.isnan(lat) else "--"
            print(f"{treatment:10s} {scenario:22s} {era:6s} {n:2d}  "
                  f"{recall_str:8s} {induced_h:10.0f}   {endo_h:8.0f}   {lat_str}")

    # SNR metric: induced/h in induced reps vs endogenous/h in idle/rich reps
    print("\n=== Isolation quality (SNR = induced/h on flush sparse / "
          "endogenous/h on idle rich) ===")
    print(f"{'treatment':10s} {'induced_flush_sparse':22s} {'endo_idle_rich':18s} {'SNR':8s}")
    for treatment in TREATMENTS:
        sub_t = df[df.treatment == treatment]
        ind = sub_t[(sub_t.scenario == "D_attack_flush") & (sub_t.audit_era == "sparse")]
        idle = sub_t[(sub_t.scenario == "A_idle") & (sub_t.audit_era == "rich")]
        ind_h = ind["induced_cascade"].sum() / max(ind["duration_s"].sum() / 3600, 1e-9)
        idle_h = idle["endogenous_unexplained"].sum() / max(idle["duration_s"].sum() / 3600, 1e-9)
        snr = ind_h / max(idle_h, 1.0)
        print(f"{treatment:10s} {ind_h:20.0f}   {idle_h:16.0f}   {snr:6.1f}")

    # Composite score across multiple criteria
    print("\n=== Composite scorecard ===")
    print(f"{'treatment':10s} {'recall_DEF':12s} {'endo_idle/h':14s} {'induced_flush_rich/h':22s} {'lat_DEF_mean':14s}")
    for treatment in TREATMENTS:
        sub_t = df[df.treatment == treatment]
        # Recall over D/E/F induced reps combined (sparse + rich for D)
        def_reps = sub_t[sub_t.scenario.isin(["D_attack_flush", "E_attack_single_rule",
                                              "F_attack_burst"])]
        recall = (def_reps["induced_cascade"] > 0).sum() / len(def_reps) if len(def_reps) else 0
        # Endogenous in idle/rich
        idle_rich = sub_t[(sub_t.scenario == "A_idle") & (sub_t.audit_era == "rich")]
        endo_idle = idle_rich["endogenous_unexplained"].sum() / max(idle_rich["duration_s"].sum() / 3600, 1e-9)
        # Induced in flush rich
        flush_rich = sub_t[(sub_t.scenario == "D_attack_flush") & (sub_t.audit_era == "rich")]
        ind_flush_rich = flush_rich["induced_cascade"].sum() / max(flush_rich["duration_s"].sum() / 3600, 1e-9)
        # Latency in D/E/F
        finite_lat = def_reps["first_lat_s"][def_reps["first_lat_s"] < float("inf")]
        lat = finite_lat.mean() if len(finite_lat) > 0 else float("nan")
        print(f"{treatment:10s} {recall:10.0%}   {endo_idle:12.0f}   {ind_flush_rich:20.0f}   {lat:12.2f}s")


if __name__ == "__main__":
    main()
