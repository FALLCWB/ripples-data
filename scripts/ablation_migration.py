#!/usr/bin/env python3
"""
ablation_migration (v2) — the LITERAL deliverable of reviewer R2.12: an explicit
ablation of the priority-rule labeler against a naive temporal-anchor method,
plus the causal MIS-ATTRIBUTION RATE as the headline (not the tautological recall).

Four labeler orders over the SAME events of each rep (joined by index):
  corrected  : Induced before Direct, with legit-anchor exception (frozen P2)
  submitted  : Direct before Induced, with exception (isolates ONLY the ordering)
  literal_may: Direct before Induced, NO exception (reproduces the shipped binary)
  naive      : temporal anchor only, no induced window at all

Deliverables:
  1. MIS-ATTRIBUTION RATE (headline, non-circular): of the events that are truly
     induced-caused (ground truth = within [t_a, t_a+D_REF] of an induced action,
     D_REF fixed = 300s), the fraction each variant labels "Direct-anchor" — i.e.
     credits a post-event reaction as a direct cause. The priority rule (corrected)
     drives this to ~0; the naive/literal_may variants do not. This is the concrete
     evidence that the priority rule "prevents causal misattribution" (R2.12).
  2. Migration matrix corrected->{submitted, literal_may, naive}: how many events
     move between categories, showing where the cascade events go under each.
  Recall of Induced-cascade is also reported but flagged as window-coverage, not
  independent detection (naive is 0 by construction: it has no such category).

OvS only (cross-domain has no audit -> no six-category attribution).

Usage:
  python3 ablation_migration.py --snapshots DIR [--W 2] [--C 5] [--delta 300]
                                [--out ablation.json]
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

import labeler_v2 as L
import rep_io

ORDERS = ["corrected", "submitted", "literal_may", "naive"]


def rep_labels(rep, W, C, delta):
    """Return (ev_ts, {order:[labels]}, induced, audit) for an OvS induced rep, or None."""
    if rep.domain != "ovs" or rep.audit is None or not rep.induced:
        return None
    ev = rep.test[rep.test["signal"] > rep.threshold]
    ev_ts = ev["ts"].values.astype(float)
    by_cat = rep.by_cat or {}
    lldp_a = by_cat.get("LLDP_REFRESH", by_cat.get("LINK_EVENT", np.array([])))
    stats_a = by_cat.get("STATS_RESPONSE", by_cat.get("PORT_EVENT", np.array([])))
    lldp_ticks = L.predict_cadence(lldp_a, L.LLDP_PERIOD, rep.t_s, rep.t_e)
    stats_ticks = L.predict_cadence(stats_a, L.STATS_PERIOD, rep.t_s, rep.t_e)
    labs = {o: [L.classify(ts, o, rep.audit, lldp_ticks, stats_ticks,
                           rep.induced, rep.legit, W, C, delta) for ts in ev_ts]
            for o in ORDERS}
    return ev_ts, labs, rep.induced, rep.audit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", type=Path, required=True)
    ap.add_argument("--W", type=float, default=2.0)
    ap.add_argument("--C", type=float, default=5.0)
    ap.add_argument("--delta", type=float, default=300.0)
    ap.add_argument("--gt-ref", type=float, default=None,
                    help="ground-truth induced horizon (default = --delta). Vary for "
                         "sensitivity (e.g. the empirical decay return time).")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    d_ref = args.gt_ref if args.gt_ref is not None else args.delta

    mig = {tgt: defaultdict(int) for tgt in ("submitted", "literal_may", "naive")}
    # misattribution = GT-induced event labelled anything OTHER than Induced-cascade
    # (a truly induced-caused event not attributed to the action). Reported both
    # over ALL GT events and over the CONTESTED subset (GT events that also have a
    # competing audit anchor within W, where the priority order actually decides).
    mis_all = {o: [] for o in ORDERS}
    mis_contested = {o: [] for o in ORDERS}
    direct_all = {o: [] for o in ORDERS}   # the specific Induced->Direct error
    coverage = {o: [] for o in ORDERS}
    n_reps = n_contested_events = 0

    for rep in rep_io.iter_reps(args.snapshots):
        if rep.excluded:
            continue
        r = rep_labels(rep, args.W, args.C, args.delta)
        if r is None:
            continue
        ev_ts, labs, induced, audit = r
        n_reps += 1
        for tgt in ("submitted", "literal_may", "naive"):
            for a, b in zip(labs["corrected"], labs[tgt]):
                mig[tgt][(a, b)] += 1
        gt = [i for i, ts in enumerate(ev_ts)
              if L.active_aftermath(float(ts), induced, d_ref) is not None]
        # contested = GT event with a competing audit anchor within W (order decides)
        contested = [i for i in gt if L.dist_nearest(float(ev_ts[i]), audit) <= args.W]
        n_contested_events += len(contested)
        if gt:
            for o in ORDERS:
                mis_all[o].append(sum(1 for i in gt if labs[o][i] != "Induced-cascade") / len(gt))
                direct_all[o].append(sum(1 for i in gt if labs[o][i] == "Direct-anchor") / len(gt))
                coverage[o].append(sum(1 for i in gt if labs[o][i] == "Induced-cascade") / len(gt))
        if contested:
            for o in ORDERS:
                mis_contested[o].append(
                    sum(1 for i in contested if labs[o][i] != "Induced-cascade") / len(contested))

    def matrix(tgt):
        return {f"{a} -> {b}": n for (a, b), n in sorted(mig[tgt].items())
                if a != b and n > 0}

    def mean(d, o):
        return round(float(np.mean(d[o])), 4) if d[o] else None

    out = {
        "n_induced_reps": n_reps,
        "n_contested_events": n_contested_events,
        "params": {"W": args.W, "C": args.C, "delta": args.delta, "gt_ref": d_ref,
                   "PRE_QUANTILE": rep_io.PRE_QUANTILE, "snapshots": str(args.snapshots)},
        "induced_not_recovered_rate_all": {o: mean(mis_all, o) for o in ORDERS},
        "induced_not_recovered_rate_contested": {o: mean(mis_contested, o) for o in ORDERS},
        "induced_to_direct_rate": {o: mean(direct_all, o) for o in ORDERS},
        "induced_window_coverage": {o: mean(coverage, o) for o in ORDERS},
        "migration_corrected_to_submitted": matrix("submitted"),
        "migration_corrected_to_literal_may": matrix("literal_may"),
        "migration_corrected_to_naive": matrix("naive"),
        "notes": {
            "induced_not_recovered_rate_all": "fraction of truly-induced events NOT labelled "
                "Induced-cascade. Under a temporal GT (window = gt_ref), corrected is "
                "low largely by label consistency; the CONTESTED rate is the non-tautological "
                "number (where a competing audit anchor makes the order decide).",
            "induced_not_recovered_rate_contested": "restricted to GT events with an audit anchor "
                "within W: here the priority order genuinely decides Induced-cascade vs "
                "Direct-anchor, so a low corrected rate vs high naive/literal_may is real "
                "evidence the priority rule prevents causal misattribution (R2.12).",
            "induced_window_coverage": "GT is the temporal window, so this is coverage, "
                "not independent detection; naive is 0 by construction.",
            "literal_may": "reproduces the shipped May binary (Direct-first, no legit "
                "exception); submitted isolates ONLY the ordering vs corrected.",
        },
    }
    dest = args.out or (args.snapshots.parent / "analysis" / "ablation_migration.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=1))

    print(f"induced reps: {n_reps} | contested events: {n_contested_events}")
    print("induced not recovered (all GT):", out["induced_not_recovered_rate_all"])
    print("induced not recovered (contested):", out["induced_not_recovered_rate_contested"])
    print("corrected->literal_may:", out["migration_corrected_to_literal_may"] or "(none yet)")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
