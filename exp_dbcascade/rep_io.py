#!/usr/bin/env python3
"""
rep_io — unified rep reader for BOTH schemas (reviewer R2.11 / Table 2 need the
cross-domain path; the OvS-only glob silently dropped Redis/Dockerd reps).

Two on-disk schemas exist:

  OvS (lab.py / scenarios.py):
    markers.json : {warmup_start_ts, controller_attached_ts, test_phase_start_ts}
    features_switch1_*.csv : 8 cols, NO header, PER-PAGE rows -> aggregate to
                             per-iteration signal = sum(change_volume>0) per ts
    provenance_*.json : audit log (six-category attribution applies)
    events.json : actions incl. inject_action / inject_flow_install

  Cross-domain (exp_crossdomain/memdump_runner.py -> Redis/Dockerd/nginx):
    markers.json : {warmup_start_ts, action_ts, observation_end_ts,
                    target_process, action_kind, ...}
    features.csv : 5 cols WITH header (ts,n_changed_pages,change_vol_bytes,
                   entropy_mean,max_page_addr_changed) -> already per-iteration,
                   signal = change_vol_bytes
    NO provenance (no audit -> no six-category attribution; magnitude/signature/
                   presence only)
    events.json : [{ts, action:"inject_action", kind}]

load_rep() normalizes both to a RepData with a per-iteration `signal` and a
common pre/test window split, so ripple_presence / decay_curve / stats work
uniformly. The labeler (attribution) still applies only where audit exists.

Signal comparability caveat: OvS signal is bytes summed per iteration over the
tracked heap pages; cross-domain signal is change_vol_bytes per iteration from
the same dumper. Both are "bytes changed since previous dump", so the presence/
decay/signature computations (which normalize per rep against that rep's own
warmup threshold) are within-rep comparable. Absolute magnitudes are NOT
cross-domain comparable, which is why amplification is a ratio, not a raw count.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

PRE_QUANTILE = 95
OVS_COLS = ["ts", "iter_index", "page_id", "x_res", "y_res",
            "change_volume", "byte_entropy_changed", "byte_entropy_full"]


@dataclass
class RepData:
    name: str
    domain: str                 # "ovs" | "crossdomain"
    scenario: str
    pre: pd.DataFrame           # columns [ts, signal] — baseline / warmup window
    test: pd.DataFrame          # columns [ts, signal] — post-action / test window
    threshold: float            # per-rep p95 of pre signal
    induced: list               # induced action timestamps (may be empty / multiple)
    legit: list                 # scripted legitimate action timestamps (OvS only)
    audit: Optional[np.ndarray] # sorted audit ts, or None (crossdomain)
    by_cat: Optional[dict]      # per-category audit ts arrays, or None
    t_s: float                  # test-window start
    t_e: float                  # test-window end
    excluded: bool = False
    reason: str = ""
    markers: dict = field(default_factory=dict)


def _ovs_per_iter(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for ts, g in df.groupby("ts"):
        a = g[g["change_volume"] > 0]
        rows.append({"ts": float(ts),
                     "signal": float(a["change_volume"].sum()) if len(a) else 0.0})
    if not rows:                                   # empty window (e.g. mid-write rep)
        return pd.DataFrame({"ts": [], "signal": []})
    return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True)


def _detect_domain(sd: Path, markers: dict) -> str:
    if "test_phase_start_ts" in markers and "controller_attached_ts" in markers:
        return "ovs"
    if "action_ts" in markers or (sd / "features.csv").exists():
        return "crossdomain"
    # fall back on file presence
    return "ovs" if list(sd.glob("features_switch1_*.csv")) else "crossdomain"


def _load_events_actions(sd: Path):
    induced, legit = [], []
    try:
        events = json.loads((sd / "events.json").read_text())
    except Exception:
        return induced, legit
    if isinstance(events, dict):
        events = events.get("events", [])
    for e in events:
        act = e.get("action", "")
        ts = e.get("ts")
        if ts is None:
            continue
        if "inject_action" in act or "inject_attack" in act:  # legacy name tolerated
            induced.append(float(ts))
        elif "flow_install" in act:
            legit.append(float(ts))
    return sorted(induced), sorted(legit)


def _load_audit(sd: Path):
    provs = list(sd.glob("provenance_*.json"))
    if not provs:
        return None, None
    from collections import defaultdict
    by_cat = defaultdict(list)
    for p in provs:
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        for dev, events in d.get("devices", {}).items():
            for e in events:
                by_cat[e.get("category", "?")].append(e.get("ts", 0) / 1000.0)
    by_cat = {c: np.array(sorted(set(v))) for c, v in by_cat.items()}
    all_a = np.array(sorted(set(t for arr in by_cat.values() for t in arr)))
    return all_a, by_cat


def load_rep(sd: Path, min_iters: int = 10) -> Optional[RepData]:
    """Load and normalize one rep dir. Returns None if no markers; returns a
    RepData with excluded=True if below the min-iterations rule."""
    if not (sd / "markers.json").exists():
        return None
    name = sd.name

    def _excl(reason, markers=None, scenario=None):
        return RepData(name, "unknown", scenario or name.split("_rep")[0],
                       None, None, 0.0, [], [], None, None, 0, 0,
                       excluded=True, reason=reason, markers=markers or {})

    # markers.json may be mid-write while the sweep is running -> guard.
    try:
        markers = json.loads((sd / "markers.json").read_text())
    except (json.JSONDecodeError, OSError) as e:
        return _excl(f"markers.json unreadable ({type(e).__name__})")
    domain = _detect_domain(sd, markers)
    induced, legit = _load_events_actions(sd)

    try:
        if domain == "ovs":
            w_s = markers.get("warmup_start_ts")
            c_s = markers.get("controller_attached_ts")
            t_s = markers.get("test_phase_start_ts")
            if None in (w_s, c_s, t_s):
                return _excl("incomplete OvS markers", markers)
            csvs = sorted(sd.glob("features_switch1_*.csv"),
                          key=lambda p: (-p.stat().st_size, p.name))
            if not csvs:
                return _excl("no features_switch1 csv", markers)
            df = pd.concat([pd.read_csv(p, header=None, names=OVS_COLS) for p in csvs],
                           ignore_index=True).drop_duplicates(subset=["ts", "page_id"])
            pre = _ovs_per_iter(df[(df["ts"] >= w_s) & (df["ts"] < c_s)])
            test = _ovs_per_iter(df[df["ts"] >= t_s])
            audit, by_cat = _load_audit(sd)
            scenario = name.split("_rep")[0]
        else:  # crossdomain
            a_ts = markers.get("action_ts")
            if a_ts is None:
                return _excl("crossdomain rep without action_ts", markers)
            fcsv = sd / "features.csv"
            if not fcsv.exists():
                alt = sorted(sd.glob("features*.csv"), key=lambda p: (-p.stat().st_size, p.name))
                fcsv = alt[0] if alt else None
            if fcsv is None:
                return _excl("no features csv", markers)
            raw = pd.read_csv(fcsv)  # header present
            raw = raw.rename(columns={"change_vol_bytes": "signal"})[["ts", "signal"]]
            raw["ts"] = raw["ts"].astype(float)
            pre = raw[raw["ts"] < a_ts].reset_index(drop=True)
            test = raw[raw["ts"] >= a_ts].reset_index(drop=True)
            t_s = float(a_ts)
            audit, by_cat = None, None
            if not induced:
                induced = [float(a_ts)]
            scenario = f"{markers.get('target_process','x')}_{markers.get('action_kind','x')}"
    except (pd.errors.ParserError, pd.errors.EmptyDataError, OSError, ValueError, KeyError) as e:
        return _excl(f"features csv unreadable ({type(e).__name__})", markers)

    if pre is None or test is None or len(pre) < min_iters or len(test) < min_iters:
        return RepData(name, domain, scenario, pre, test, 0.0, induced, legit,
                       audit, by_cat, t_s, 0,
                       excluded=True,
                       reason=f"iters pre={0 if pre is None else len(pre)} "
                              f"test={0 if test is None else len(test)} < {min_iters}",
                       markers=markers)

    thr = float(np.percentile(pre["signal"].values, PRE_QUANTILE))
    if thr <= 0:                      # all-zero warmup -> every non-zero iter would flag
        return RepData(name, domain, scenario, pre, test, thr, induced, legit,
                       audit, by_cat, t_s, float(test["ts"].max()),
                       excluded=True, reason="warmup threshold == 0 (degenerate baseline)",
                       markers=markers)
    t_e = float(test["ts"].max())
    return RepData(name, domain, scenario, pre, test, thr, induced, legit,
                   audit, by_cat, t_s, t_e, markers=markers)


def signal_between(rep: "RepData", lo: float, hi: float) -> np.ndarray:
    """Per-iteration signal values with ts in [lo, hi), searched across BOTH the
    pre (baseline/warmup) and test windows. Needed because the pre-action window
    [t_a-Delta, t_a] lands in `test` for OvS (test starts before the action) but
    in `pre` for cross-domain (test starts AT the action)."""
    vals = []
    for frame in (rep.pre, rep.test):
        if frame is None or len(frame) == 0:
            continue
        ts = frame["ts"].values
        sig = frame["signal"].values
        m = (ts >= lo) & (ts < hi)
        if m.any():
            vals.append(sig[m])
    return np.concatenate(vals) if vals else np.array([])


def iter_reps(snapshots: Path, min_iters: int = 10):
    """Yield RepData for every rep dir under snapshots (skips non-rep dirs)."""
    for sd in sorted(snapshots.iterdir()):
        if sd.is_dir() and (sd / "markers.json").exists():
            r = load_rep(sd, min_iters)
            if r is not None:
                yield r


if __name__ == "__main__":
    import sys
    snap = Path(sys.argv[1])
    n_ovs = n_cd = n_excl = 0
    for r in iter_reps(snap):
        if r.excluded:
            n_excl += 1
            print(f"EXCLUDED {r.name}: {r.reason}")
            continue
        n_ovs += r.domain == "ovs"
        n_cd += r.domain == "crossdomain"
        print(f"{r.name:40s} [{r.domain:11s}] scen={r.scenario} "
              f"pre={len(r.pre)} test={len(r.test)} thr={r.threshold:.0f} "
              f"induced={len(r.induced)} audit={'-' if r.audit is None else len(r.audit)}")
    print(f"\novs={n_ovs} crossdomain={n_cd} excluded={n_excl}")
