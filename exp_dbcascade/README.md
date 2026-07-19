# Controlled ground-truth oracle for the labeler attribution order (R2.12)

A controlled Python enrichment service backed by PostgreSQL, representative of
the enrich-and-cache pattern of production ingest paths. An ingest action
(SIGUSR1) triggers a delayed, multi-stage reactive cascade (fetch a value from a
mock weather API with real latency, then staged writes to PostgreSQL + in-memory
cache/aggregate updates, each self-logged); after the cascade, an independent
operator action (SIGUSR2, a brief direct in-place update to a memory region
disjoint from the cascade's) is issued.

Ground truth is assigned by **provenance**, from each action's own write
timestamps in the audit log, NOT from the labeler's classification windows
(W, C, Delta), so the correctness test is not circular:

- REACTIVE window `[t0, cascade_end + 0.5s]`: the cascade is the only active
  cause, so every above-threshold event is a reaction -> true Induced-cascade.
  This is the discriminating, non-circular test of the ordering rule.
- ADMIN window `[max(t_leg, react_end), t_leg + 1.5s]`: the operator action's own brief write ->
  true Direct-anchor. This window coincides with the labeler's near-anchor
  exception, so it is a consistency check, not an independent one.
- AMBIENT: above-threshold events in neither window (background/decay), excluded
  from the correctness test and reported separately.

`dbcascade_labeler_eval.py` runs the paper's own labeler (labeler_v2.classify)
in two orders differing only in precedence (corrected = Induced-before-Direct;
submitted = Direct-before-Induced).

Result (`dbcascade_result.json`, 18 reps, all valid): on the 155 reactive-window
events the Direct-before-Induced order misattributes every reaction to
Direct-anchor (recovers none as Induced-cascade) via the reactor's own write-log
entry, while the priority order recovers all 155. The outcome is identical in all
18 repetitions, so no bootstrap CI is reported (it would be tautological on a
zero-variance vector); the contrast is structural (it demonstrates the failure
mode, not its prevalence), and the load-bearing result is the Direct-first
failure. The priority order's recovery of all reactions is the recall side and
means it loses no reaction, not that its labels are otherwise precise: on the
same reps it labels 90 of the 102 ambient (background) events Induced-cascade, so
over-attribution is bounded separately (by the paper's shifted-anchor control),
not by this oracle. The priority order labels the independent action's own
footprint, written to a disjoint memory region, Direct-anchor (all 36 events).
`data/` holds the per-rep features.csv / audit.jsonl / markers.json.

Run:  bash setup_dbcascade.sh && bash collect_dbcascade_v2.sh <out> 18
      python3 dbcascade_labeler_eval.py <out>
