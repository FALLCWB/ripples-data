# Claim manifest

Every quantity reported in the manuscript is listed here with the level at which a
reader can reconstruct it from this package. The levels are:

- **R1 recomputed** — recomputed from released per-repetition observations by running
  the named script. The number is produced, not read back.
- **R2 regenerated** — regenerated from a released summary file. The pipeline that
  produced the summary is in the package, but its input corpus is not, so running the
  script reproduces the figure or table cell rather than recomputing the statistic.
- **R3 measured summary** — a measurement recorded at collection time and released as a
  summary. It cannot be recomputed here because the underlying raw observations
  (process-memory dumps, per-run schedules) are not redistributed.
- **R4 not reconstructible** — depends on the raw capture corpus or on the SDN testbed
  harness, neither of which is part of this package.

Raw process-memory captures hold live process memory and are too large to host online.
The SDN testbed harness that drives the Open vSwitch collection is not part of this
package. Those two exclusions are what separate R1/R2 from R3/R4.

| Manuscript item | Quantity | Level | Produced by |
|---|---|---|---|
| §VII-A, Table 2 | Six-category rates per OvS scenario | R1 | `scripts/labeler_v2.py` over `data/processed/ovs_recollection_aggregates/` |
| §VII-A | Rich-vs-sparse residual to zero, paired on flush reps | R1 | `scripts/labeler_v2.py` |
| §VII-A | Audit density 114 → 306 entries per repetition | R3 | recorded at collection |
| §VII-B | Matched-window occupancy 0.31 vs 0.30; 19/30 | R1 | `scripts/placebo_control.py` |
| §VII-B | Naive 1.39× vs placebo 1.04× at 30 s | R1 | `scripts/placebo_control.py` |
| §VII-B | Placebo-adjusted step 1.25×, 24/29, p = 3.3e-4 | R1 | `scripts/placebo_control.py` |
| §VII-B | Anchor-spacing sensitivity 1.28× → 1.25× | R1 | `scripts/placebo_control.py --win` sweep |
| §VII-B | Between-arm 1.25× vs 0.96×, ratio 1.31×, p = 3.6e-5 | R1 | `scripts/between_arm_test.py` |
| §VII-B | Sham arm 0.85× / 1.02× / 0.99× | R1 | `scripts/between_arm_test.py` |
| §VII-B | Lag profile, six bins, per-scenario ranges, Bonferroni | R1 | `scripts/lag_profile.py` |
| §VII-B | Shifted-anchor control: 2.0 vs 289 events, 0.7% | R1 | `scripts/presence_null.py` |
| §VII-B, Table 3 | Redis last supra-threshold time (0.6 / 0.7 / ≥179.8 s) | R1 | `scripts/surface_threshold.py` |
| §VII-B, Table 3 | Dockerd presence 8 / 20 / 57 / 100% with Wilson CIs | R1 | `scripts/surface_threshold.py` |
| §VII-C, Fig. 2a | Per-hour rates 1346 / 1234 / 1345; ρ = −0.13 | R1 | `scripts/surface_excess_spearman.py` |
| §VII-C | Conditional magnitude ρ = 0.02, p = 0.94, n = 20 | R1 | `scripts/placebo_control.py` |
| §VII-C | Cascade-present cosine 0.89; 3.12×; CI [2.67, 3.63] | R1 | `scripts/signature_cascade_present.py` |
| §VII-C | Within-daemon, cascade-present: Dockerd 2.313×; OvS 1.01×; Redis not estimable (one cascade-present action) | R1 | `scripts/signature_cascade_present.py` |
| §VII-C | Full-corpus within-daemon sensitivity: Redis 1.85×, Dockerd 2.23×, OvS 1.00× (includes splash-only conditions) | R1 | `scripts/signature_replevel_perm.py` |
| §VII-C | Corpus-wide 2.37×, ANOSIM R = 0.53, PERMANOVA F = 17.8 | R1 | `scripts/signature_replevel_perm.py` |
| §VII-C, Fig. 2b | Per-scenario cosines 0.85 / 0.60 / 0.94 with CIs | R1 | `scripts/within_scenario_ci.py` |
| §VII-C | OvS no-action control 1.02× vs 1.00× | R1 | `scripts/signature_cascade_present.py` |
| §VII-D, Table 4 | Redis and Dockerd peak, baseline, amplification | R2 | `scripts/regen_figs_data.py` over released aggregates |
| §VII-D | Dockerd repetition accounting 16/12, 16/10, 17/7, 17/7 | R1 | `scripts/exclusion_accounting.py` |
| §VII-E, Fig. 4 | Observer CPU, RSS, dump latency, cadence, throughput | R3 | recorded by `exp_overhead/`; released as summary |
| §VII-E | Cost-versus-heap points (1 MB, 39 MB, 140 MB) | R3 | recorded at collection |
| §VII-F, Fig. 5 | Default-GC amplification 10.9 / 12.6 / 11.4×, ρ = 0.09 | R2 | regenerated from released default-GC summary |
| §VII-G, Fig. 6 | Cross-environment similarity and 4.0× vs 2.0× | R2 | regenerated from released robustness summary |
| §VII-H | Overlap arm, 24 cells, multiplicity note, p = 0.47 | R1 | `scripts/overlap_analysis.py` |
| §VII-I | Label migration 14,429 events, 84% | R1 | `scripts/ablation_migration.py` |
| §VII-J | Oracle 155/102/90/12; recall 100%, precision 63% / 75% | R1 | `exp_dbcascade/` plus `scripts/labeler_v2.py` |
| §VI | Labeler grid, 60 cells, recall 0.23 / 0.54 / 1.00 | R1 | `scripts/grid_wcd.py` |
| §VI | Threshold sensitivity 29/30 for max, 30/30 otherwise | R1 | `scripts/compare_thresholds.py` |
| §IX | OvS capture ceiling: 33 pages, censored feature values | R1 | `scripts/build_ovs_aggregates.py` |
| — | Raw OvS process-memory dumps | R4 | not redistributed |
| — | Per-repetition OvS collection schedules | R4 | produced by the SDN harness, not in package |
| — | SDN testbed harness | R4 | not part of this package |

What the grid in §VI evaluates per cell is label stability and induced-cascade
retention. It does not evaluate a per-cell induced false-association rate; the
false-association reading is the shifted-anchor control, which is a single descriptive
matched-window contrast rather than a per-cell quantity.
