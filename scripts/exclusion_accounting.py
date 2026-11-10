#!/usr/bin/env python3
"""Repetition accounting for the GOGC=off cross-domain arm (reviewer R2.10, R4.5).

Counts attempted, kept and excluded repetitions per Dockerd action directly from
the released rep directories. A repetition is kept when it carries both a
completion marker and a feature stream; the rest are incomplete docker-in-docker
batches, which is the pre-registered exclusion criterion.

R4.5 (camera-ready): the same counts split by launch mode, inferred from the
directory name. exp_crossdomain/run_dockerd.sh stamps a directory with the
launch time in whole seconds (10 digits) and runs one repetition at a time. The
batch launches stamp it in nanoseconds (19 digits); the batch launch command is
not part of this package, and the launch mode is inferred from the stamp format
and from the stamps of one launch falling within the same second (the project
log records the Dockerd batch as four actions run in two pairs with high
parallelism). Repetitions whose nanosecond stamps share the same second form one
launch: in the released data, two launches of twenty concurrent repetitions and
two later readback re-runs of one and two repetitions. The script also recomputes the ripple-presence result of R2.11 (the
warmup-anchored null of scripts/revision_numbers.py, same rule, same pooled null)
and reports it by launcher for the two rungs that kept repetitions from both
launchers, after checking that the pooled values equal the persisted R2.11 values.

Output: data/processed/exclusion_accounting.json
"""
import collections, glob, json, os, re, sys
from pathlib import Path

import numpy as np

PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG / "scripts"))
from revision_numbers import _rows  # same reader as the published R2.11 presence

CD = PKG / "data/crossdomain"
ACTIONS = {"dockerd_docker_inspect": "docker version (readback)",
           "dockerd_docker_run_1": "1 container",
           "dockerd_docker_run_10": "10 containers",
           "dockerd_docker_run_50": "50 containers"}
AFT = 180.0  # aftermath window of R2.11, seconds


def launcher(d):
    stamp = d.rsplit("_", 1)[1]
    if re.fullmatch(r"\d{10}", stamp):
        return "sequential", None
    if re.fullmatch(r"\d{19}", stamp):
        return "parallel", stamp[:10]
    raise ValueError(f"unrecognised stamp in {d}")


def is_kept(d):
    return os.path.exists(f"{d}/markers.json") and os.path.exists(f"{d}/features.csv")


reps = []  # one row per attempted repetition
for prefix, label in ACTIONS.items():
    for d in sorted(glob.glob(str(CD / f"{prefix}_rep*"))):
        if not os.path.isdir(d):
            continue
        mode, launch = launcher(os.path.basename(d))
        reps.append({"action": prefix, "dir": d, "launcher": mode, "launch": launch,
                     "kept": is_kept(d), "has_markers": os.path.exists(f"{d}/markers.json")})

# Accounting per action (unchanged R2.10 block) and per action and launcher (R4.5)
per_action, by_launcher = {}, {}
for prefix, label in ACTIONS.items():
    rs = [r for r in reps if r["action"] == prefix]
    kept = sum(r["kept"] for r in rs)
    per_action[prefix] = {"action": label, "attempted": len(rs), "kept": kept,
                          "excluded": len(rs) - kept,
                          "exclusion_reason": "no completion marker (incomplete docker-in-docker batch)"}
    by_launcher[prefix] = {}
    for mode in ("sequential", "parallel"):
        sub = [r for r in rs if r["launcher"] == mode]
        k = sum(r["kept"] for r in sub)
        by_launcher[prefix][mode] = {"attempted": len(sub), "kept": k, "excluded": len(sub) - k}

excluded = [r for r in reps if not r["kept"]]
excl_by_launcher = collections.Counter(r["launcher"] for r in excluded)
excl_without_marker = sum(1 for r in excluded if not r["has_markers"])

launches = {}
for r in reps:
    if r["launcher"] != "parallel":
        continue
    g = launches.setdefault(r["launch"], {"size": 0, "kept": 0, "excluded": 0, "per_action": {}})
    g["size"] += 1
    g["kept" if r["kept"] else "excluded"] += 1
    pa = g["per_action"].setdefault(r["action"], {"attempted": 0, "kept": 0})
    pa["attempted"] += 1
    pa["kept"] += r["kept"]

# R2.11 presence, same rule as scripts/revision_numbers.py::r2_11_presence
null, counts = [], []
for r in reps:
    L = _rows(r["dir"], 2)  # change_vol_bytes
    if not L:
        continue
    a, rows = L
    W = [(ts, cv) for ts, cv in rows if ts < a]
    P = [(ts, cv) for ts, cv in rows if ts >= a]
    if len(W) < 10 or not P:
        continue
    thr = np.percentile([cv for _, cv in W], 95)
    counts.append((r, sum(1 for ts, cv in P if a <= ts <= a + AFT and cv > thr)))
    wmax = max(ts for ts, _ in W)
    null.append(sum(1 for ts, cv in W if ts >= wmax - AFT and cv > thr))
q95 = float(np.percentile(np.array(null), 95))

pooled = {}
for prefix in ACTIONS:
    cs = [c for r, c in counts if r["action"] == prefix]
    pooled[prefix] = {"n": len(cs), "present": sum(1 for c in cs if c > q95)}

# The pooled values must equal the persisted R2.11 values the paper reports
persisted = json.loads((PKG / "data/processed/revision_numbers.json").read_text())["R2.11_ripple_presence_calibrated"]
assert len(null) == persisted["null_draws"], (len(null), persisted["null_draws"])
assert q95 == persisted["null_q95"], (q95, persisted["null_q95"])
for prefix in ACTIONS:
    p = persisted["presence"][prefix.replace("dockerd_", "")]
    assert (pooled[prefix]["n"], pooled[prefix]["present"]) == (p["n"], p["present"]), (prefix, pooled[prefix], p)

presence_by_launcher = {}
for prefix in ACTIONS:
    row = {}
    for mode in ("sequential", "parallel"):
        cs = [c for r, c in counts if r["action"] == prefix and r["launcher"] == mode]
        row[mode] = {"n": len(cs), "present": sum(1 for c in cs if c > q95)}
    presence_by_launcher[prefix] = row

for prefix, label in ACTIONS.items():
    a, s, p = per_action[prefix], by_launcher[prefix]["sequential"], by_launcher[prefix]["parallel"]
    print(f"{label:26s} attempted {a['attempted']:3d} kept {a['kept']:3d} excluded {a['excluded']:3d} | "
          f"sequential {s['kept']}/{s['attempted']} kept | parallel {p['kept']}/{p['attempted']} kept, {p['excluded']} excluded")
print(f"excluded total {len(excluded)}: sequential {excl_by_launcher['sequential']}, parallel {excl_by_launcher['parallel']}; "
      f"without completion marker {excl_without_marker}")
for k, g in sorted(launches.items()):
    print(f"parallel launch {k}: {g['size']} repetitions, {g['kept']} kept, {g['excluded']} excluded, {g['per_action']}")
print(f"presence null: {len(null)} draws, q95 {q95} (equals persisted R2.11)")
for prefix, row in presence_by_launcher.items():
    print(f"presence {ACTIONS[prefix]:26s} {row}")

Path(PKG / "data/processed/exclusion_accounting.json").write_text(json.dumps({
    "note": ("Repetition accounting for the GOGC=off cross-domain arm, counted from the "
             "released rep directories: a repetition is kept when it carries both a "
             "completion marker and a feature stream."),
    "per_action": per_action,
    "R4.5_by_launcher": {
        "note": ("Launch mode inferred from the directory name (the batch launch command is "
                 "not part of this package): 10-digit second stamp = sequential, "
                 "exp_crossdomain/run_dockerd.sh, one repetition at a time; 19-digit "
                 "nanosecond stamp = batch launch; batch-launched repetitions are grouped into launches by the stamp's second: two "
                 "launches of twenty concurrent repetitions and two later readback re-runs of "
                 "one and two repetitions. "
                 "Batch, parallelism and load were not varied separately, so any association "
                 "with load is descriptive."),
        "per_action": by_launcher,
        "excluded_total": len(excluded),
        "excluded_by_launcher": dict(excl_by_launcher),
        "excluded_without_completion_marker": excl_without_marker,
        "parallel_launches": launches,
    },
    "R4.5_presence_by_launcher": {
        "note": ("R2.11 ripple presence (warmup-anchored null, same rule and pooled null as "
                 "scripts/revision_numbers.py) split by launcher; pooled values checked equal "
                 "to the persisted R2.11 values. Excluded repetitions lack the completion "
                 "marker that timestamps the action, so their presence cannot be computed. "
                 "Descriptive only: with 3 against 9 and 5 against 5 repetitions no test can "
                 "resolve a difference by launcher, and launcher is confounded with launch, "
                 "date and load."),
        "null_draws": len(null), "null_q95": q95,
        "per_action": presence_by_launcher,
    },
}, indent=2))
print("wrote data/processed/exclusion_accounting.json")
