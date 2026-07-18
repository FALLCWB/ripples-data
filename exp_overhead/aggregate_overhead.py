#!/usr/bin/env python3
"""
aggregate_overhead — summarize an overhead run (R2#7/R4#4): observer cost from
the *_dumper.json files and target throughput with-vs-without from the paired
*.json files. Filters by a minimum timestamp so a re-run's batch is not mixed
with an earlier invalid batch. Observer records with pages_per_dump_mean == 0
are dropped (they measured dumping nothing -> invalid).

Usage: aggregate_overhead.py RESULTS_DIR [--target redis] [--min-ts 0]
"""
import argparse
import glob
import json
import os
from statistics import mean, pstdev


def ts_of(path):
    base = os.path.basename(path).replace(".json", "")
    parts = base.split("_")
    return int(parts[-2]) if base.endswith("_dumper") else int(parts[-1])


def load(results_dir, pat, min_ts):
    items = []
    for f in glob.glob(os.path.join(results_dir, pat)):
        try:
            t = ts_of(f)
        except ValueError:
            continue
        if t >= min_ts:
            items.append((t, json.load(open(f))))
    return [d for _, d in sorted(items, key=lambda x: x[0])]


def ms(xs):
    return f"{mean(xs):.1f} +/- {pstdev(xs):.1f}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results_dir")
    ap.add_argument("--target", default="redis")
    ap.add_argument("--min-ts", type=int, default=0)
    args = ap.parse_args()
    T = args.target

    dmp = [d for d in load(args.results_dir, f"overhead_{T}_with_rep*_dumper.json", args.min_ts)
           if d.get("pages_per_dump_mean", 0) > 0]
    print(f"== OBSERVER COST (n={len(dmp)}, valid dumps only) ==")
    if dmp:
        print(f"  pages/dump      : {ms([d['pages_per_dump_mean'] for d in dmp])}")
        print(f"  CPU % of wall   : {ms([d['observer_cpu_pct_of_wall'] for d in dmp])}")
        print(f"  RSS MB          : {ms([d['observer_rss_bytes'] / 1e6 for d in dmp])}")
        print(f"  dump lat p50 ms : {ms([d['dump_latency_s']['p50'] * 1000 for d in dmp])}")
        print(f"  dump lat p95 ms : {ms([d['dump_latency_s']['p95'] * 1000 for d in dmp])}")
        print(f"  cadence overrun%: {ms([d['cadence_overrun_pct'] for d in dmp])}")

    def bench(arm):
        xs = load(args.results_dir, f"overhead_{T}_{arm}_rep*.json", args.min_ts)
        xs = [d for d in xs if isinstance(d, dict) and d.get("target") == T and "bench_result" in d]
        need = 2 if T == "redis" else 1        # redis: SET+GET; dockerd: single wall time
        vals = [[float(x) for x in d["bench_result"]] for d in xs if len(d["bench_result"]) >= need]
        return vals

    print(f"== TARGET THROUGHPUT ({T}) ==")
    res = {}
    for arm in ("with", "without"):
        vals = bench(arm)
        if not vals:
            continue
        if T == "redis":
            sset = [v[0] for v in vals]; sget = [v[1] for v in vals]
            print(f"  {arm:8s} (n={len(vals)})  SET ops/s {ms(sset)}   GET ops/s {ms(sget)}")
            res[arm] = {"set": mean(sset), "get": mean(sget), "n": len(vals)}
        else:  # dockerd: single wall-time number
            w = [v[0] for v in vals]
            print(f"  {arm:8s} (n={len(vals)})  wall s {ms(w)}")
            res[arm] = {"wall": mean(w), "n": len(vals)}
    if "with" in res and "without" in res and T == "redis":
        for k in ("set", "get"):
            delta = 100.0 * (res["with"][k] - res["without"][k]) / res["without"][k]
            print(f"  impact {k.upper()}: {delta:+.1f}% (with vs without)")


if __name__ == "__main__":
    main()
