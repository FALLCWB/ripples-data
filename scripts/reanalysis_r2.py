#!/usr/bin/env python3
"""Re-analyses added in the second revision, in response to reviewer concerns
about (a) the audit-coverage comparison being run on different repetitions,
(b) the signature-separation permutation not being blocked by daemon, and
(c) the robustness figure measuring within-environment self-similarity rather
than cross-environment reproduction. Writes data/processed/reanalysis_r2.json.

The daemon-blocked permutation is recomputed here from the released pairwise
CSV. The paired audit and cross-environment results are computed from the
dense-audit recollection corpus (labels_corrected_{rich,sparse}*.json and the
r46 environment reps), which is not redistributed (multi-GB, live memory); the
computed values are persisted below so every reported number is available.
"""
import csv
import json
import os
from collections import defaultdict
from pathlib import Path

import numpy as np

PKG = Path(__file__).resolve().parent.parent
PAIRS = PKG / "data/processed/signature_pairwise_similarity.csv"
OUT = PKG / "data/processed/reanalysis_r2.json"


def daemon_blocked_permutation(n_perm=2000, seed=42):
    """Permute action labels WITHIN each daemon (the block for action
    discriminability) and test within-action vs across-action separation."""
    rows = list(csv.DictReader(open(PAIRS)))
    dae = lambda s: s.split("-")[0]
    r2s = {}
    for r in rows:
        r2s[r["rep_a"]] = r["scenario_a"]
        r2s[r["rep_b"]] = r["scenario_b"]
    rng = np.random.default_rng(seed)
    out = {}
    for scope in ("OvS", "Redis", "Dockerd", "ALL"):
        daes = [scope] if scope != "ALL" else ["OvS", "Redis", "Dockerd"]
        P = [(r["rep_a"], r["rep_b"], float(r["cosine_similarity"])) for r in rows
             if dae(r["scenario_a"]) in daes and dae(r["scenario_b"]) == dae(r["scenario_a"])]

        def split(lab):
            w = [c for a, b, c in P if lab[a] == lab[b]]
            ac = [c for a, b, c in P if lab[a] != lab[b]]
            return np.mean(w), np.mean(ac)
        w0, a0 = split(r2s)
        obs = w0 - a0
        reps_by_d = defaultdict(list)
        for rp, sc in r2s.items():
            if dae(sc) in daes:
                reps_by_d[dae(sc)].append(rp)
        cnt = 0
        for _ in range(n_perm):
            perm = dict(r2s)
            for da, rl in reps_by_d.items():
                scs = [r2s[rp] for rp in rl]
                for rp, sc in zip(rl, rng.permutation(scs)):
                    perm[rp] = sc
            w, a = split(perm)
            if (w - a) >= obs:
                cnt += 1
        out[scope] = {"within_action": round(float(w0), 3), "across_action": round(float(a0), 3),
                      "separation": round(float(w0 / a0), 2), "perm_p": round((cnt + 1) / (n_perm + 1), 4)}
    return out


# Values computed from the non-redistributed dense-audit recollection corpus.
PAIRED_AUDIT = {
    "note": "Same flush repetitions labeled under both audit densities "
            "(labels_corrected_rich/sparse, W=2 C=5 D=300); isolates audit coverage.",
    "n_reps": 10, "audit_entries_rich": 306, "audit_entries_sparse": 114,
    "induced_cascade_per_h": {"rich": 1345, "sparse": 1345, "delta": 0,
                              "interpretation": "audit-independent (Algorithm 1 Induced rule ignores the audit set)"},
    "endogenous_plus_periodic_per_h": {"rich": 0, "sparse": 1480,
                                       "interpretation": "dense audit resolves previously-unexplained events to Direct-anchor"},
}
CROSS_ENV_ROBUSTNESS = {
    "note": "cross-environment signature comparison, Redis 7/Alpine vs Redis 6/Debian",
    "post_over_pre_mean_by_action": {"SET": {"redis7": 1.00, "redis6": 1.01},
                                     "MSET": {"redis7": 1.00, "redis6": 1.01},
                                     "FLUSHDB": {"redis7": 1.37, "redis6": 1.00}},
    "finding": "the page-change ripple signal on the two secondary (cross-host) environments is weak "
               "(post-action within 1.0-1.4x of baseline), so a cross-environment SIGNATURE comparison is "
               "under-powered on this data; the clearly-measured configuration-dependent quantity is the "
               "peak/baseline amplification (FLUSHDB 4.0x on Redis7/Alpine vs 2.0x on Redis6/Debian).",
}


def oracle_confusion():
    d = json.loads((PKG / "exp_dbcascade/dbcascade_result.json").read_text())
    rw, aw, amb = d["reactive_window"], d["admin_window"], d["ambient"]
    reactive = rw["n_events"]
    r_ind = rw["corrected_induced_first"]["events_recovered"]
    admin = aw["n_events"]
    a_dir = aw["corrected_to_direct"]
    amb_ind = amb["corrected"]["Induced-cascade"]
    amb_dir = amb["corrected"]["Direct-anchor"]
    ind_pred = r_ind + amb_ind + (admin - a_dir)
    dir_pred = (reactive - r_ind) + a_dir + amb_dir
    return {
        "induced": {"recall": round(r_ind / reactive, 3),
                    "precision": round(r_ind / ind_pred, 3)},
        "direct": {"precision": round(a_dir / dir_pred, 3)},
        "background_recall": 0.0,
        "note": "priority order validates the attribution ORDER (recall), not label precision; "
                "background is untestable on this single-aftermath oracle (all ambient events fall in Delta).",
    }


if __name__ == "__main__":
    result = {
        "daemon_blocked_permutation": daemon_blocked_permutation(),
        "paired_audit_coverage": PAIRED_AUDIT,
        "cross_environment_robustness": CROSS_ENV_ROBUSTNESS,
        "oracle_confusion_matrix": oracle_confusion(),
    }
    OUT.write_text(json.dumps(result, indent=1))
    print(json.dumps(result, indent=1))
    print(f"\nwrote {OUT}", file=os.sys.stderr)
