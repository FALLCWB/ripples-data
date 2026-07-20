#!/usr/bin/env python3
"""Build the released per-iteration OvS aggregates from the raw recollection.

The raw per-page capture (1.3 GB of live memory) is too large to host online, so
the released package ships the per-iteration aggregates this script produces.
It is included so the reduction step is auditable end to end: given the raw
snapshots it regenerates data/processed/ovs_recollection_aggregates/<rep>.csv
byte for byte.

Aggregation matches scripts/regen_figs_data.py::per_iter_aggregates (one row per
0.5 s dump, over pages with change_volume > 0). The phase markers and the induced
action timestamp are carried on every row so downstream scripts need no other
input.

Usage:
  OVS_SNAPSHOTS=/path/to/recollection/snapshots python3 scripts/build_ovs_aggregates.py
  [--scenarios D_flush,E_single_rule] [--out DIR]
"""
import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
SNAPSHOTS = Path(os.environ.get("OVS_SNAPSHOTS", "/tmp/lab_snapshots"))
COLS = ["ts", "iter_index", "page_id", "x_res", "y_res",
        "change_volume", "byte_entropy_changed", "byte_entropy_full"]
DEFAULT_SCENARIOS = ("A_idle", "B_flow_install", "C_ping_sustained", "D_flush",
                     "E_single_rule", "F_burst", "G_overlap_30s", "H_overlap_150s")


def load_raw(sd: Path):
    """The post-action CSV holds the complete warmup-to-aftermath timeline."""
    cands = sorted(sd.glob("features_switch1_*_post_action.csv"))
    if not cands:
        cands = sorted(sd.glob("features_switch1_*.csv"))
    if not cands:
        return None
    return pd.read_csv(cands[-1], header=None, names=COLS)


def action_times(sd: Path):
    """All induced-action timestamps in the repetition, in order.

    The overlap scenarios carry two; the single-action scenarios carry one or
    none (the routine-condition scenarios A/B/C).
    """
    ev_path = sd / "events.json"
    if not ev_path.exists():
        return []
    ts = [float(e["ts"]) for e in json.loads(ev_path.read_text())
          if e.get("action") in ("inject_action", "inject_attack")]
    return sorted(ts)


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


def build_rep(sd: Path, out_dir: Path):
    markers_path = sd / "markers.json"
    if not markers_path.exists():
        return None
    markers = json.loads(markers_path.read_text())
    raw = load_raw(sd)
    if raw is None or raw.empty:
        return None
    agg = per_iter_aggregates(raw, int(raw["page_id"].max()))
    if agg.empty:
        return None
    acts = action_times(sd)
    agg["warmup_start_ts"] = markers.get("warmup_start_ts")
    agg["controller_attached_ts"] = markers.get("controller_attached_ts")
    agg["test_phase_start_ts"] = markers.get("test_phase_start_ts")
    agg["action_ts"] = acts[0] if acts else np.nan
    agg["max_page"] = int(raw["page_id"].max())
    if len(acts) > 1:
        agg["action2_ts"] = acts[1]
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{sd.name}.csv"
    agg.to_csv(out, index=False)
    return out, len(agg), len(acts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", type=Path, default=SNAPSHOTS)
    ap.add_argument("--out", type=Path, default=PROC / "ovs_recollection_aggregates")
    ap.add_argument("--scenarios", default=",".join(DEFAULT_SCENARIOS))
    args = ap.parse_args()

    prefixes = tuple(s.strip() for s in args.scenarios.split(",") if s.strip())
    reps = [d for d in sorted(args.snapshots.iterdir())
            if d.is_dir() and d.name.startswith(prefixes)]
    if not reps:
        raise SystemExit(f"no repetitions matching {prefixes} under {args.snapshots}")

    n_ok = 0
    for sd in reps:
        r = build_rep(sd, args.out)
        if r is None:
            print(f"  skipped {sd.name} (no markers/raw features)")
            continue
        out, n_iter, n_act = r
        n_ok += 1
        print(f"  {out.name}: {n_iter} iterations, {n_act} induced action(s)")
    print(f"wrote {n_ok}/{len(reps)} repetitions to {args.out}")


if __name__ == "__main__":
    main()
