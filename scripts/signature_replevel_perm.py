#!/usr/bin/env python3
"""
signature_replevel_perm — rep-level permutation test + bootstrap CI for the
E2 signature separation, run DIRECTLY on the preserved pairwise-similarity
matrix (reviewers R2#9, R4#3: pairwise comparisons are NOT independent, so the
within-vs-across separation needs inference at the repetition level).

The 108 signatures generate 5778 pairwise cosines (564 within-scenario, 5214
across). Treating those pairs as independent overstates n. This test permutes
the rep->scenario assignment (preserving scenario group sizes) and recomputes
the within/across split over the SAME fixed cosines, so the null is "signatures
carry no scenario identity" evaluated at the level of the 108 reps, not the
pairs. No raw data is needed: the preserved matrix already fixes every pair's
cosine; only the labels move.

Input:  data/processed/signature_pairwise_similarity.csv
        (rep_a, rep_b, scenario_a, scenario_b, same_scenario, cosine_similarity)
Output: stats + JSON. Reproduces the preserved within 0.7346 / across 0.3095.
"""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

SEED = 42


def load_pairs(csv_path):
    reps = {}                                   # rep name -> scenario (ground truth)
    rows = []                                   # (rep_a, rep_b, cosine)
    with open(csv_path) as f:
        for r in csv.DictReader(f):
            reps[r["rep_a"]] = r["scenario_a"]
            reps[r["rep_b"]] = r["scenario_b"]
            rows.append((r["rep_a"], r["rep_b"], float(r["cosine_similarity"])))
    rep_names = sorted(reps)
    idx = {name: i for i, name in enumerate(rep_names)}
    labels = np.array([reps[name] for name in rep_names])
    a_idx = np.array([idx[a] for a, _, _ in rows])
    b_idx = np.array([idx[b] for _, b, _ in rows])
    vals = np.array([v for _, _, v in rows])
    if len(vals) == 0:
        raise SystemExit(f"no pairs read from {csv_path}")
    if np.isnan(vals).any():                        # trust-boundary guard: never
        raise SystemExit(f"{int(np.isnan(vals).sum())} NaN cosine(s) in {csv_path}")
    return rep_names, labels, a_idx, b_idx, vals


def split_stats(labels, a_idx, b_idx, vals):
    same = labels[a_idx] == labels[b_idx]
    return vals[same].mean(), vals[~same].mean()


def perm_test(labels, a_idx, b_idx, vals, n_perm, rng):
    w0, a0 = split_stats(labels, a_idx, b_idx, vals)
    obs = w0 - a0
    count = 0
    for _ in range(n_perm):
        w, a = split_stats(rng.permutation(labels), a_idx, b_idx, vals)
        if (w - a) >= obs:
            count += 1
    return w0, a0, obs, (count + 1) / (n_perm + 1)


def anosim_permanova(labels, a_idx, b_idx, vals, n_perm, rng):
    """Confirmatory NAMED tests on the same rep-label permutation (reviewers like
    a canonical name): ANOSIM R (Clarke 1993, rank-based) and PERMANOVA pseudo-F
    (Anderson 2001, sums of squared distances). Both permute the OBJECT (rep)
    labels, which is what resolves pairwise non-independence. Input is cosine
    SIMILARITY; both tests use the cosine DISTANCE d = 1 - cosine."""
    from scipy.stats import rankdata
    n = len(labels)
    d = 1.0 - vals
    d2 = d * d
    ranks = rankdata(d)                             # average ranks, fixed across perms
    m = len(vals)                                   # = n(n-1)/2
    uniq = sorted(set(labels))
    a = len(uniq)
    gmap = {g: k for k, g in enumerate(uniq)}
    gid = np.array([gmap[x] for x in labels])
    ss_total = d2.sum() / n

    def stats(g):
        ga, gb = g[a_idx], g[b_idx]
        within = ga == gb
        r = (ranks[~within].mean() - ranks[within].mean()) / (m / 2.0)
        ng = np.bincount(g, minlength=a)
        ss_within = float(np.sum(np.bincount(ga[within], weights=d2[within],
                                             minlength=a) / ng))
        f = ((ss_total - ss_within) / (a - 1)) / (ss_within / (n - a))
        return r, f

    r_obs, f_obs = stats(gid)
    cr = cf = 0
    for _ in range(n_perm):
        r, f = stats(rng.permutation(gid))
        if r >= r_obs:
            cr += 1
        if f >= f_obs:
            cf += 1
    return {"anosim_R": round(float(r_obs), 4), "anosim_p": round((cr + 1) / (n_perm + 1), 6),
            "permanova_pseudoF": round(float(f_obs), 3),
            "permanova_p": round((cf + 1) / (n_perm + 1), 6),
            "note": "confirmatory; ANOSIM/PERMANOVA permutation on 1-cosine distance, same rep labels"}


def boot_ci(rep_names, labels, a_idx, b_idx, vals, n_boot, rng):
    """Rep-level bootstrap: resample reps within scenario, keep only pairs whose
    BOTH endpoints are in the resample, recompute within/across/ratio."""
    n = len(rep_names)
    by_scn = defaultdict(list)
    for i, s in enumerate(labels):
        by_scn[s].append(i)
    # pair endpoints as sets for fast membership per draw
    wm, am, ratio = [], [], []
    same_all = labels[a_idx] == labels[b_idx]
    for _ in range(n_boot):
        take = []
        for s, idxs in by_scn.items():
            take += list(rng.choice(idxs, size=len(idxs), replace=True))
        mult = np.bincount(take, minlength=n)          # how many times each rep drawn
        # a pair contributes mult[a]*mult[b] times (resampling with replacement)
        w = mult[a_idx] * mult[b_idx]
        if w.sum() == 0:
            continue
        wsum = (vals * w * same_all).sum(); wcnt = (w * same_all).sum()
        asum = (vals * w * ~same_all).sum(); acnt = (w * ~same_all).sum()
        if wcnt == 0 or acnt == 0:
            continue
        wmean, amean = wsum / wcnt, asum / acnt
        wm.append(wmean); am.append(amean)
        if amean:
            ratio.append(wmean / amean)
    q = lambda x: [round(float(np.percentile(x, 2.5)), 4),
                   round(float(np.percentile(x, 97.5)), 4)]
    return {"within_ci": q(wm), "across_ci": q(am), "ratio_ci": q(ratio)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path,
                    default=Path(__file__).parent.parent / "data/processed/signature_pairwise_similarity.csv")
    ap.add_argument("--n-perm", type=int, default=10000)
    ap.add_argument("--n-boot", type=int, default=10000)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    rng = np.random.default_rng(SEED)

    rep_names, labels, a_idx, b_idx, vals = load_pairs(args.csv)
    w, a, obs, p = perm_test(labels, a_idx, b_idx, vals, args.n_perm, rng)
    ci = boot_ci(rep_names, labels, a_idx, b_idx, vals, args.n_boot, rng)
    # Confirmatory named tests reuse rng AFTER the primary block, so the primary
    # numbers (validated to reproduce the preserved finding) stay byte-identical.
    named = anosim_permanova(labels, a_idx, b_idx, vals, args.n_perm, rng)
    out = {
        "n_reps": len(rep_names), "n_pairs": len(vals),
        "n_scenarios": len(set(labels)),
        "within_mean": round(float(w), 4), "across_mean": round(float(a), 4),
        "ratio": round(float(w / a), 3),
        "perm_stat": round(float(obs), 4), "perm_p": round(float(p), 6),
        "n_perm": args.n_perm, "seed": SEED, **ci,
        "sustained": bool(p < 0.05 and (w / a) >= 1.5),
        "confirmatory": named,
    }
    dest = args.out or (args.csv.parent / "signature_replevel_perm.json")
    dest.write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    print(f"wrote {dest}")


def _selfcheck():
    # Two tight clusters => within must beat across and permutation must reject.
    # 5+5 reps: C(10,5)=252 partitions, so the true split's p ~ 2/252 < 0.05
    # (3+3 gives a floor of ~0.10 and would fail spuriously).
    rng = np.random.default_rng(0)
    labels = np.array(["A"] * 5 + ["B"] * 5)
    pairs = [(i, j) for i in range(10) for j in range(i + 1, 10)]
    a_idx = np.array([i for i, _ in pairs]); b_idx = np.array([j for _, j in pairs])
    vals = np.array([0.9 if labels[i] == labels[j] else 0.1 for i, j in pairs])
    w, a, obs, p = perm_test(labels, a_idx, b_idx, vals, 2000, rng)
    assert w > a and obs > 0 and p < 0.05, (w, a, obs, p)
    # boot_ci must also flag the separation (ratio CI strictly above 1) and the
    # named tests must agree (ANOSIM R > 0, PERMANOVA p small).
    ci = boot_ci([str(i) for i in range(10)], labels, a_idx, b_idx, vals, 500, rng)
    assert ci["ratio_ci"][0] > 1.0, ci
    named = anosim_permanova(labels, a_idx, b_idx, vals, 2000, rng)
    assert named["anosim_R"] > 0 and named["permanova_p"] < 0.05, named
    print("selfcheck ok")


if __name__ == "__main__":
    import sys
    if "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        main()
