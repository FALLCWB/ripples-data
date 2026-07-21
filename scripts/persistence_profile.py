#!/usr/bin/env python3
"""WITHDRAWN. This script produced the persistence reading reported in an earlier
revision. Its comparator anchor sat two window lengths before the action while the
persistence window ran 300 s from that anchor, so the comparison window contained
the action and most of its aftermath. The reading was withdrawn by the authors.
Duration is reported by scripts/lag_profile.py, whose comparator windows all end at
or before the action. The file is kept so the withdrawn analysis remains inspectable.
"""
raise SystemExit(
    "persistence_profile.py is withdrawn: its comparator window contained the "
    "action. Use scripts/lag_profile.py for duration."
)

# --- original implementation retained below for inspection ---
# #!/usr/bin/env python3
# """Per-repetition persistence of the post-action cascade (OvS induced scenarios).
# 
# The manuscript states that ripples persist "for minutes" across the 300 s
# aftermath window. This script turns that statement into a distribution against
# the harder of the two available baselines.
# 
# Ruler. Counting supra-threshold iterations does not separate the aftermath from
# the pre-action test phase in this corpus: with the controller attached, the
# pre-action phase already runs at the same supra-threshold occupancy as the
# aftermath (see scripts/presence_null.py). What separates the two is the
# MAGNITUDE of the excursion, so persistence is measured on excess above the
# repetition's warmup 95th percentile rather than on event counts.
# 
# Method. Each repetition is split into BIN-second bins. A bin's score is the sum
# of (signal - threshold) over its supra-threshold iterations. The reference
# distribution is the set of bin scores in a PRE-window of the same repetition,
# immediately before the action, with the controller already attached; a bin is
# called active when its score exceeds the 95th percentile of that reference. The
# comparison is therefore within-repetition and against the live pre-action
# baseline, not against the quiet warmup.
# 
# Reported per repetition: the last active bin (right-censored at the window
# edge), whether the cascade is still active at each checkpoint, and the fraction
# of aftermath bins that are active.
# 
# Output: data/processed/persistence_profile.json, persistence_per_rep.csv.
# 
# Usage: python3 scripts/persistence_profile.py [--bin 10] [--pre 250]
# """
# import argparse
# import csv
# import json
# import statistics as st
# from pathlib import Path
# 
# import numpy as np
# import pandas as pd
# 
# PROC = Path(__file__).resolve().parent.parent / "data" / "processed"
# AGG = PROC / "ovs_recollection_aggregates"
# SIG = "change_volume_sum"
# AFTERMATH_S = 300.0
# PRE_Q = 95
# REF_Q = 95
# INDUCED = ("D_flush", "E_single_rule", "F_burst")
# CHECKPOINTS = (60, 150, 200, 240, 290)
# 
# 
# def bin_excess(df, thr, lo, hi, bin_s):
#     """Excess mass per bin over [lo, hi), as (bin_start_offset, score) pairs."""
#     out = []
#     start = lo
#     while start + bin_s <= hi:
#         w = df[(df["ts"] >= start) & (df["ts"] < start + bin_s)]
#         if len(w) == 0:
#             out.append((start - lo, 0.0))
#         else:
#             sup = w[w[SIG] > thr]
#             out.append((start - lo, float((sup[SIG] - thr).sum())))
#         start += bin_s
#     return out
# 
# 
# def rep_profile(path: Path, bin_s: float, pre_s: float):
#     df = pd.read_csv(path)
#     ws, ca = df["warmup_start_ts"].iloc[0], df["controller_attached_ts"].iloc[0]
#     action_ts = float(df["action_ts"].iloc[0])
#     if not np.isfinite(action_ts):
#         return None
#     warm = df[(df["ts"] >= ws) & (df["ts"] < ca)]
#     if len(warm) < 10:
#         return None
#     thr = float(np.percentile(warm[SIG], PRE_Q))
# 
#     ref = bin_excess(df, thr, action_ts - pre_s, action_ts, bin_s)
#     post = bin_excess(df, thr, action_ts, action_ts + AFTERMATH_S, bin_s)
#     if len(ref) < 5 or not post:
#         return None
#     ref_thr = float(np.percentile([s for _, s in ref], REF_Q))
# 
#     active = [(off, s) for off, s in post if s > ref_thr]
#     row = {
#         "rep": path.stem,
#         "scenario": next(s for s in INDUCED if path.stem.startswith(s)),
#         "iteration_threshold": round(thr, 1),
#         "bin_reference_threshold": round(ref_thr, 1),
#         "n_bins": len(post),
#         "n_active_bins": len(active),
#         "active_bin_fraction": round(len(active) / len(post), 4),
#         "first_active_s": round(active[0][0], 1) if active else None,
#         "last_active_s": round(active[-1][0] + bin_s, 1) if active else 0.0,
#     }
#     for t in CHECKPOINTS:
#         row[f"active_at_{t}"] = int(any(off + bin_s > t for off, _ in active))
#     return row
# 
# 
# def summarize(rows, label):
#     last = [r["last_active_s"] for r in rows]
#     frac = [r["active_bin_fraction"] for r in rows]
#     out = {
#         "label": label,
#         "n_reps": len(rows),
#         "last_active_s": {
#             "median": round(st.median(last), 1),
#             "min": round(min(last), 1),
#             "max": round(max(last), 1),
#             "iqr": [round(float(np.percentile(last, 25)), 1),
#                     round(float(np.percentile(last, 75)), 1)],
#         },
#         "active_bin_fraction_median": round(st.median(frac), 4),
#         "still_active_fraction": {},
#     }
#     for t in CHECKPOINTS:
#         k = sum(r[f"active_at_{t}"] for r in rows)
#         out["still_active_fraction"][str(t)] = {
#             "n_active": k, "n_reps": len(rows), "fraction": round(k / len(rows), 3)}
#     return out
# 
# 
# def main():
#     ap = argparse.ArgumentParser()
#     ap.add_argument("--bin", type=float, default=10.0, help="bin length in seconds")
#     ap.add_argument("--pre", type=float, default=250.0,
#                     help="length of the pre-action reference window in seconds")
#     args = ap.parse_args()
# 
#     rows = []
#     for path in sorted(AGG.glob("*.csv")):
#         if not path.stem.startswith(INDUCED):
#             continue
#         r = rep_profile(path, args.bin, args.pre)
#         if r is not None:
#             rows.append(r)
#     if not rows:
#         raise SystemExit(f"no induced repetitions found under {AGG}")
# 
#     per_scenario = {sc: summarize([r for r in rows if r["scenario"] == sc], sc)
#                     for sc in INDUCED}
#     out = {
#         "params": {"bin_s": args.bin, "pre_reference_s": args.pre,
#                    "iteration_threshold": f"p{PRE_Q} of warmup {SIG}",
#                    "bin_reference": f"p{REF_Q} of the pre-action bin scores of the same repetition",
#                    "aftermath_s": AFTERMATH_S,
#                    "checkpoints_s": list(CHECKPOINTS),
#                    "source": "data/processed/ovs_recollection_aggregates"},
#         "note": ("A bin is active when its excess mass exceeds the 95th percentile of the "
#                  "same repetition's pre-action bins, so roughly one bin in twenty is "
#                  "expected active by chance. last_active_s is right-censored at the "
#                  "window length: a value at the edge means the cascade was still running "
#                  "when observation ended."),
#         "pooled": summarize(rows, "all induced"),
#         "per_scenario": per_scenario,
#     }
#     (PROC / "persistence_profile.json").write_text(json.dumps(out, indent=2))
# 
#     with open(PROC / "persistence_per_rep.csv", "w", newline="") as f:
#         w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
#         w.writeheader()
#         w.writerows(rows)
# 
#     p = out["pooled"]
#     print(f"{p['n_reps']} induced reps, {args.bin:.0f} s bins vs pre-action p{REF_Q}: "
#           f"last active bin median {p['last_active_s']['median']} s "
#           f"(IQR {p['last_active_s']['iqr']}, range {p['last_active_s']['min']}"
#           f"-{p['last_active_s']['max']} s), active bins "
#           f"{p['active_bin_fraction_median']:.0%} of the window")
#     for t in CHECKPOINTS:
#         f = p["still_active_fraction"][str(t)]
#         print(f"  still active at {t:>3} s: {f['n_active']}/{f['n_reps']}")
#     for sc, s in per_scenario.items():
#         print(f"  {sc}: median last {s['last_active_s']['median']} s, "
#               f"active bins {s['active_bin_fraction_median']:.0%}")
# 
# 
# if __name__ == "__main__":
#     main()
# 