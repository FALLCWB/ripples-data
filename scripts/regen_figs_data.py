#!/usr/bin/env python3
"""Regenerate per-figure CSVs from real lab snapshot data.

This script does NOT use a classifier. An event in this pipeline is a
test-phase iteration whose raw per-iteration signal exceeds the 95th
percentile of the same signal in the pre-action (warmup) window of the same rep.
The signal is change_volume_sum (bytes mutated per iteration). This is
the same detection-free threshold used by signature_validation.py and
gen_crossdomain.py.

Produces three CSVs under data/processed/:

  fig2_sparse_cascade_per_rep.csv
      Per-rep Induced-cascade event counts and per-hour rates for the
      three sparse-era induced-action scenarios (D_flush,
      E_single_rule, F_burst).

  fig5_temporal_signal.csv
      Raw change_volume_sum signal over time for a representative flush
      rep, with the pre-action threshold annotated.

  fig6_feature_distributions.csv
      Per-iteration feature aggregates for "baseline" (idle reps +
      pre-action period of the flush rep) and "ripple" (the post-action
      aftermath iterations whose signal exceeded the per-rep threshold).
"""
import os
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

SNAPSHOTS = Path(os.environ.get("OVS_SNAPSHOTS", "/tmp/lab_snapshots"))
OUT = Path(__file__).parent.parent / "data" / "processed"
OUT.mkdir(parents=True, exist_ok=True)

COLS = [
    "ts", "iter_index", "page_id", "x_res", "y_res",
    "change_volume", "byte_entropy_changed", "byte_entropy_full",
]

FEATS_AGG = [
    "n_active", "region_low", "region_midlow", "region_midhigh", "region_high",
    "page_id_mean", "page_id_std", "page_id_max",
    "change_volume_sum", "change_volume_mean", "change_volume_max",
    "entropy_changed_mean", "entropy_changed_max",
    "entropy_full_mean", "entropy_full_std",
]

W_TIGHT = 2.0
AFTERMATH_S = 300
CASCADE_LOOKBACK_S = 5.0
LLDP_PERIOD = 3.0
STATS_PERIOD = 5.0
CTX_AUDIT_CTRL = 2.0
CTX_CADENCE = 1.0
CTX_AUDIT_SW = 2.0
CTX_CADENCE_SW = 1.0

# Single-column signal used as the per-iteration event indicator.
SIGNAL_COL = "change_volume_sum"


def per_iter_aggregates(df: pd.DataFrame, max_page: int) -> pd.DataFrame:
    aggs = []
    for ts, g in df.groupby("ts"):
        a = g[g["change_volume"] > 0]
        if len(a) == 0:
            continue
        pids = a["page_id"].values
        aggs.append({
            "ts": ts,
            "n_active": len(a),
            "page_id_mean": float(pids.mean()),
            "page_id_std": float(pids.std()) if len(a) > 1 else 0.0,
            "page_id_max": float(pids.max()),
            "region_low": int((pids < max_page * 0.25).sum()),
            "region_midlow": int(((pids >= max_page * 0.25) & (pids < max_page * 0.50)).sum()),
            "region_midhigh": int(((pids >= max_page * 0.50) & (pids < max_page * 0.75)).sum()),
            "region_high": int((pids >= max_page * 0.75).sum()),
            "change_volume_sum": float(a["change_volume"].sum()),
            "change_volume_mean": float(a["change_volume"].mean()),
            "change_volume_max": float(a["change_volume"].max()),
            "entropy_changed_mean": float(a["byte_entropy_changed"].mean()),
            "entropy_changed_max": float(a["byte_entropy_changed"].max()),
            "entropy_full_mean": float(a["byte_entropy_full"].mean()),
            "entropy_full_std": float(a["byte_entropy_full"].std()) if len(a) > 1 else 0.0,
        })
    return pd.DataFrame(aggs)


def load_audit(sd: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    by_cat = defaultdict(list)
    for p in sd.glob("provenance_*.json"):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        for _, events in d.get("devices", {}).items():
            for e in events:
                by_cat[e.get("category", "?")].append(e.get("ts", 0) / 1000.0)
    for c in by_cat:
        by_cat[c] = np.array(sorted(set(by_cat[c])))
    all_audit = np.array(sorted(set(t for arr in by_cat.values() for t in arr)))
    return all_audit, dict(by_cat)


def predict_cadence(anchor: np.ndarray, period: float, t_s: float, t_e: float) -> np.ndarray:
    if len(anchor) == 0:
        return np.array([])
    origin = anchor[0]
    lo = -int((origin - t_s) / period) - 1
    hi = int((t_e - origin) / period) + 2
    return np.array([
        origin + i * period for i in range(lo, hi)
        if t_s - 2 <= origin + i * period <= t_e + 2
    ])


def dist_nearest(ts: float, arr: np.ndarray) -> float:
    if len(arr) == 0:
        return float("inf")
    idx = np.searchsorted(arr, ts)
    d = float("inf")
    if idx > 0:
        d = min(d, ts - arr[idx - 1])
    if idx < len(arr):
        d = min(d, arr[idx] - ts)
    return d


def context(ts, audit, lldp_ticks, stats_ticks):
    d_a = dist_nearest(ts, audit)
    d_c = min(dist_nearest(ts, lldp_ticks), dist_nearest(ts, stats_ticks))
    if d_a <= CTX_AUDIT_CTRL or d_c <= CTX_CADENCE:
        return "Controller"
    if d_a > CTX_AUDIT_SW and d_c > CTX_CADENCE_SW:
        return "Switch"
    return "ND"


def classify(ts, audit, lldp_ticks, stats_ticks, action_ts):
    """Six-category labeller. action_ts is the timestamp of the induced
    state-mutation event for the rep (None for non-induced scenarios)."""
    d_a = dist_nearest(ts, audit)
    # Algorithm 1: scan Induced-cascade before Direct-anchor, so an action's own
    # reactive audit entries are not read back as the cause of the cascade they
    # belong to (a reaction cannot precede its cause). See the manuscript's
    # priority-order rationale; labeler_v2 is the definitive implementation.
    if action_ts is not None and action_ts <= ts <= action_ts + AFTERMATH_S:
        return "Induced-cascade"
    if d_a <= W_TIGHT:
        return "Direct-anchor"
    if len(audit):
        idx = np.searchsorted(audit, ts - CASCADE_LOOKBACK_S)
        if idx < len(audit) and audit[idx] < ts - W_TIGHT:
            return "Reactive-cascade"
    ctx = context(ts, audit, lldp_ticks, stats_ticks)
    if ctx == "Controller":
        return "Periodic-gap"
    if ctx == "Switch":
        return "Endogenous-unexplained"
    return "Indeterminate"


def find_action_ts(sd: Path):
    try:
        events = json.loads((sd / "events.json").read_text())
        for e in events:
            if "inject_attack" in e.get("action", "") or "inject_action" in e.get("action", ""):
                return float(e.get("ts"))
    except Exception:
        pass
    return None


# Backward-compatible alias for callers that imported the old name.
find_action_ts = find_action_ts


def load_rep_features(sd: Path) -> tuple[pd.DataFrame, int]:
    csvs = list(sd.glob("features_switch1_*.csv"))
    if not csvs:
        raise FileNotFoundError(f"no features csv in {sd}")
    frames = [pd.read_csv(p, header=None, names=COLS) for p in csvs]
    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["ts", "page_id"])
    df = df.sort_values("ts").reset_index(drop=True)
    max_page = int(df["page_id"].max())
    return df, max_page


PRE_QUANTILE = 95  # percentile of pre-action signal used as threshold


def pre_threshold(pre_signal: np.ndarray) -> float:
    """Per-rep threshold: 95th percentile of pre-action signal.

    Robust to single-iteration outliers in the warmup window while
    remaining purely descriptive (no ML model). A test-phase iteration
    is flagged as an event when its signal exceeds this value."""
    if len(pre_signal) == 0:
        return float("inf")
    return float(np.percentile(pre_signal, PRE_QUANTILE))


def flag_above_threshold(agg_pre: pd.DataFrame, agg_test: pd.DataFrame) -> np.ndarray:
    """Boolean mask over agg_test: signal > 95th percentile of agg_pre."""
    if len(agg_pre) == 0:
        return np.zeros(len(agg_test), dtype=bool)
    threshold = pre_threshold(agg_pre[SIGNAL_COL].values)
    return agg_test[SIGNAL_COL].values > threshold


def analyze_rep(sd: Path) -> dict | None:
    """Per-rep cascade counts using raw-threshold event flagging."""
    try:
        markers = json.loads((sd / "markers.json").read_text())
        p1_s = markers["warmup_start_ts"]
        p1_e = markers["controller_attached_ts"]
        t_s = markers["test_phase_start_ts"]
    except Exception:
        return None
    df, max_page = load_rep_features(sd)
    agg_p1 = per_iter_aggregates(df[(df["ts"] >= p1_s) & (df["ts"] < p1_e)], max_page)
    df_test = df[df["ts"] >= t_s]
    agg_test = per_iter_aggregates(df_test, max_page)
    if len(agg_p1) < 10 or len(agg_test) < 10:
        return None
    action_ts = find_action_ts(sd)

    ext_mask = flag_above_threshold(agg_p1, agg_test)
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
    return {
        "scenario": sd.name.split("_rep")[0],
        "rep_id": sd.name,
        "direct_anchor": int(counts.get("Direct-anchor", 0)),
        "reactive_cascade": int(counts.get("Reactive-cascade", 0)),
        "induced_cascade": int(counts.get("Induced-cascade", 0)),
        "periodic_gap": int(counts.get("Periodic-gap", 0)),
        "endogenous_unexplained": int(counts.get("Endogenous-unexplained", 0)),
        "indeterminate": int(counts.get("Indeterminate", 0)),
        "duration_s": round(dur_s, 2),
        "induced_cascade_count": int(counts.get("Induced-cascade", 0)),
        "per_hour_rate": round(
            counts.get("Induced-cascade", 0) * 3600.0 / max(dur_s, 1.0), 1
        ),
        "threshold_used": float(np.percentile(agg_p1[SIGNAL_COL], PRE_QUANTILE)),
        "n_test_iters": int(len(agg_test)),
        "n_ext": int(ext_mask.sum()),
    }


def is_sparse_era(d: Path) -> bool:
    # legacy capture filename, kept so first-collection snapshots still load
    prov_files = sorted(d.glob("provenance_*_post_attack.json"))
    if not prov_files:
        return False
    try:
        j = json.loads(prov_files[0].read_text())
        for dev_entries in j.get("devices", {}).values():
            for entry in dev_entries:
                if entry.get("category") == "STATS_RESPONSE":
                    return False
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------
# Fig 2 — sparse-era per-rep Induced-cascade rates.
# --------------------------------------------------------------------------
def build_fig2() -> pd.DataFrame:
    targets = []
    for d in sorted(SNAPSHOTS.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        if not (name.startswith("D_flush") or
                name.startswith("E_single_rule") or
                name.startswith("F_burst")):
            continue
        if not is_sparse_era(d):
            continue
        targets.append(d)

    rows = []
    for sd in targets:
        r = analyze_rep(sd)
        if r is None:
            print(f"  [fig2] skipped {sd.name} (insufficient data)", file=sys.stderr)
            continue
        rows.append(r)
        print(f"  [fig2] {sd.name}: induced={r['induced_cascade_count']} "
              f"dur={r['duration_s']}s rate={r['per_hour_rate']}/h "
              f"threshold={r['threshold_used']:.0f}")

    df = pd.DataFrame(rows)
    out = OUT / "fig2_sparse_cascade_per_rep.csv"
    df.to_csv(out, index=False)
    print(f"  -> {out}  ({len(df)} reps)")
    return df


# --------------------------------------------------------------------------
# Scenario decomposition (Table I) — all OvS scenarios at both audit levels.
# --------------------------------------------------------------------------
def build_scenario_decomposition() -> pd.DataFrame:
    """Per-rep six-category breakdown across all OvS scenarios.

    The output drives Table I in the paper. Rates are per-hour and use
    the raw-threshold event flagging defined in flag_above_threshold."""
    prefixes = [
        "A_idle", "B_flow_install", "C_ping_sustained",
        "D_flush", "E_single_rule", "F_burst",
    ]
    rows = []
    for d in sorted(SNAPSHOTS.iterdir()):
        if not d.is_dir():
            continue
        if not any(d.name.startswith(p) for p in prefixes):
            continue
        r = analyze_rep(d)
        if r is None:
            continue
        r["audit_era"] = "sparse" if is_sparse_era(d) else "rich"
        rows.append(r)

    df = pd.DataFrame(rows)
    out = OUT / "scenario_decomposition.csv"
    df.to_csv(out, index=False)
    print(f"  -> {out}  ({len(df)} reps)")
    return df


# --------------------------------------------------------------------------
# Fig 5 — raw temporal signal for a representative D_flush rep.
# --------------------------------------------------------------------------
def build_fig5() -> pd.DataFrame:
    cand = sorted(SNAPSHOTS.glob("D_flush_rep11_*"))
    if not cand:
        cand = sorted(SNAPSHOTS.glob("D_flush_rep10_*"))
        print("  [fig5] rep11 missing, falling back to rep10")
    if not cand:
        cand = sorted(SNAPSHOTS.glob("D_flush_rep*"))
        print(f"  [fig5] using {cand[0].name}")
    sd = cand[0]
    print(f"  [fig5] using {sd.name}")
    markers = json.loads((sd / "markers.json").read_text())
    p1_s = markers["warmup_start_ts"]
    p1_e = markers["controller_attached_ts"]
    t_s = markers["test_phase_start_ts"]
    action_ts = find_action_ts(sd)
    df, max_page = load_rep_features(sd)
    agg_p1 = per_iter_aggregates(df[(df["ts"] >= p1_s) & (df["ts"] < p1_e)], max_page)
    agg_test = per_iter_aggregates(df[df["ts"] >= t_s], max_page)
    if len(agg_p1) < 10:
        raise RuntimeError(f"fig5: only {len(agg_p1)} warmup iterations; need >=10")

    threshold = float(np.percentile(agg_p1[SIGNAL_COL], PRE_QUANTILE))
    window = agg_test[(agg_test["ts"] >= action_ts - 30) &
                      (agg_test["ts"] <= action_ts + 200)].copy()
    window["t_rel_s"] = window["ts"] - action_ts
    out_df = window[["t_rel_s", SIGNAL_COL]].rename(
        columns={SIGNAL_COL: "signal"}
    )
    out_df = out_df.sort_values("t_rel_s").reset_index(drop=True)
    out_df["threshold"] = threshold

    out = OUT / "fig5_temporal_signal.csv"
    out_df.to_csv(out, index=False)
    print(f"  -> {out}  ({len(out_df)} iterations, "
          f"signal min={out_df['signal'].min():.0f} "
          f"max={out_df['signal'].max():.0f}, threshold={threshold:.0f})")
    return out_df


# --------------------------------------------------------------------------
# Fig 6 — per-iteration feature distributions, ripple vs baseline.
# --------------------------------------------------------------------------
def build_fig6() -> pd.DataFrame:
    """Build feature distributions for ripple-iterations vs baseline-iterations.

    "Ripple iteration" = a test-phase iteration in the post-action aftermath
    [action_ts, action_ts+AFTERMATH_S] whose change_volume_sum exceeds the
    95th percentile of the same signal in the warmup window of the same rep.

    "Baseline iteration" = an iteration drawn from the warmup phase of the
    same rep plus the full timeline of three idle reps. No threshold
    filtering; this represents the typical per-iteration footprint of OvS
    when no induced action is present."""
    cand = sorted(SNAPSHOTS.glob("D_flush_rep11_*"))
    if not cand:
        cand = sorted(SNAPSHOTS.glob("D_flush_rep10_*"))
        print("  [fig6] rep11 missing, falling back to rep10")
    if not cand:
        cand = sorted(SNAPSHOTS.glob("D_flush_rep*"))
    action_sd = cand[0]
    print(f"  [fig6] ripple source: {action_sd.name}")

    markers = json.loads((action_sd / "markers.json").read_text())
    p1_s = markers["warmup_start_ts"]
    p1_e = markers["controller_attached_ts"]
    t_s = markers["test_phase_start_ts"]
    action_ts = find_action_ts(action_sd)
    df, max_page = load_rep_features(action_sd)
    agg_warm = per_iter_aggregates(df[(df["ts"] >= p1_s) & (df["ts"] < p1_e)], max_page)
    agg_test = per_iter_aggregates(df[df["ts"] >= t_s], max_page)
    if len(agg_warm) < 10:
        raise RuntimeError(f"fig6: only {len(agg_warm)} warmup iterations; need >=10")

    threshold = float(np.percentile(agg_warm[SIGNAL_COL], PRE_QUANTILE))
    ripple = agg_test[
        (agg_test[SIGNAL_COL] > threshold) &
        (agg_test["ts"] >= action_ts) &
        (agg_test["ts"] <= action_ts + AFTERMATH_S)
    ].copy()

    idle_dirs = sorted(SNAPSHOTS.glob("A_idle_rep*"))
    idle_dirs = [d for d in idle_dirs
                 if int(d.name.split("_")[-1]) >= 1779792500][:3]
    print(f"  [fig6] baseline idle reps: {[d.name for d in idle_dirs]}")
    idle_frames = []
    for d in idle_dirs:
        try:
            df_i, max_page_i = load_rep_features(d)
        except FileNotFoundError:
            continue
        markers_i = json.loads((d / "markers.json").read_text())
        t_s_i = markers_i.get("test_phase_start_ts", df_i["ts"].min())
        idle_frames.append(per_iter_aggregates(df_i[df_i["ts"] >= t_s_i], max_page_i))
    baseline = pd.concat([agg_warm] + idle_frames, ignore_index=True)

    feat_map = {
        "Active pages":           "n_active",
        "Volume sum":             "change_volume_sum",
        "High-region pages":      "region_high",
        "Entropy (changed)":      "entropy_changed_mean",
    }
    rows = []
    for label, col in feat_map.items():
        for kind, src in (("baseline", baseline), ("ripple", ripple)):
            for v in src[col].fillna(0).values:
                rows.append({"feature": label, "kind": kind, "value": float(v)})
    out_df = pd.DataFrame(rows)
    out = OUT / "fig6_feature_distributions.csv"
    out_df.to_csv(out, index=False)
    n_base = len(baseline)
    n_rip = len(ripple)
    print(f"  -> {out}  (n_baseline_iters={n_base}, n_ripple_iters={n_rip})")
    for label, col in feat_map.items():
        bm = baseline[col].fillna(0).mean()
        bs = baseline[col].fillna(0).std()
        rm = ripple[col].fillna(0).mean()
        rs = ripple[col].fillna(0).std()
        print(f"    {label:24s} baseline={bm:.2f}±{bs:.2f}  ripple={rm:.2f}±{rs:.2f}")
    return out_df


def main():
    print("== scenario_decomposition ==")
    build_scenario_decomposition()
    print("\n== fig2 ==")
    build_fig2()
    print("\n== fig5 ==")
    build_fig5()
    print("\n== fig6 ==")
    build_fig6()
    print("\nDone.")


if __name__ == "__main__":
    main()
