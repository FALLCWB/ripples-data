#!/usr/bin/env python3
"""Repetition accounting for the GOGC=off cross-domain arm (reviewer R2.10).

Counts attempted, kept and excluded repetitions per Dockerd action directly from
the released rep directories. A repetition is kept when it carries both a
completion marker and a feature stream; the rest are incomplete docker-in-docker
batches, which is the pre-registered exclusion criterion.

Output: data/processed/exclusion_accounting.json
"""
import glob, json, os, collections
from pathlib import Path

ACTIONS = {"dockerd_docker_inspect": "docker version (readback)",
           "dockerd_docker_run_1": "1 container",
           "dockerd_docker_run_10": "10 containers",
           "dockerd_docker_run_50": "50 containers"}
out = {}
for prefix, label in ACTIONS.items():
    dirs = [d for d in glob.glob(f"data/crossdomain/{prefix}_rep*") if os.path.isdir(d)]
    kept = [d for d in dirs if os.path.exists(f"{d}/markers.json") and os.path.exists(f"{d}/features.csv")]
    out[prefix] = {"action": label, "attempted": len(dirs), "kept": len(kept),
                   "excluded": len(dirs) - len(kept),
                   "exclusion_reason": "no completion marker (incomplete docker-in-docker batch)"}
    print(f"{label:26s} attempted {len(dirs):3d}  kept {len(kept):3d}  excluded {len(dirs)-len(kept):3d}")
Path("data/processed/exclusion_accounting.json").write_text(json.dumps(
    {"note": ("Repetition accounting for the GOGC=off cross-domain arm, counted from the "
              "released rep directories: a repetition is kept when it carries both a "
              "completion marker and a feature stream."), "per_action": out}, indent=2))
