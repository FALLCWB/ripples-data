# Claim manifest

Every quantity reported in the manuscript is listed here with the level at which a
reader can reconstruct it from this package, and with the file that actually produces
it. The levels are:

- **R1 recomputed** — recomputed from released per-repetition observations by running
  the named script. The number is produced, not read back.
- **R2 regenerated** — regenerated from a released summary or label file. The pipeline
  is in the package, but its input corpus is not, so running the named script
  reproduces the figure or table cell rather than recomputing the statistic.
- **R3 measured summary** — recorded at collection time and released as a summary. It
  cannot be recomputed here because the underlying raw observations (process-memory
  dumps, per-run schedules) are not redistributed.
- **R4 not reconstructible** — depends on the raw capture corpus or on the SDN testbed
  harness, neither of which is part of this package.

Raw process-memory captures hold live process memory and are too large to host online.
The SDN testbed harness that drives the Open vSwitch collection is not part of this
package. `data/snapshots/` is empty in the release: any script taking `--snapshots`
requires that corpus and therefore cannot reach R1 here, whatever it can do on the
collection host.

| Manuscript item | Quantity | Level | Produced by |
|---|---|---|---|
| §VII-A, Table 2 | Six-category rates per OvS scenario | R2 | `scripts/regen_ovs_figs.py`, reading `labels_corrected_{sparse,rich}_W2.0_C5.0_D300.json`; `scripts/labeler_v2.py` regenerates those labels but needs `--snapshots` |
| §VII-A | Rich-vs-sparse residual to zero, paired on flush reps | R1 | `scripts/reanalysis_r2.py` (`paired_audit_coverage`) |
| §VII-A | Audit density 114 to 306 entries per repetition | R1 | `scripts/reanalysis_r2.py` (`paired_audit_coverage`) |
| §VII-B | Matched-window occupancy 0.31 vs 0.30; 19/30 | R1 | `scripts/presence_null.py` (`paired_excess`) |
| §VII-B | Naive 1.39x vs placebo 1.04x at 30 s | R1 | `scripts/placebo_control.py` |
| §VII-B | Placebo-adjusted step 1.25x, 24/29, p = 3.3e-4 | R1 | `scripts/placebo_control.py` |
| §VII-B | Window sweep 1.30 / 1.25 / 1.13 / 1.16 | R1 | `scripts/placebo_control.py` (`window_sweep`) |
| §VII-B | Anchor-spacing reading 1.28x under contiguous anchors | R4 | superseded; the released code implements two-window spacing only and does not reproduce it |
| §VII-B | Between-arm 1.25x vs 0.96x, ratio 1.31x | R1 | `scripts/between_arm_test.py` |
| §VII-B | Sham arm 0.85x / 1.02x / 0.99x | R1 | `scripts/placebo_control.py` (`sham_anchor_no_action_scenarios`) |
| §VII-B | Lag profile, six bins, per-scenario ranges, Bonferroni | R1 | `scripts/lag_profile.py` |
| §VII-B | Shifted-anchor control: 2.0 vs 289 events, 0.7% | R3 | `data/processed/revision_numbers.json` (`R4.2_shifted_anchor`); computed over a snapshot corpus not redistributed |
| §VII-B, Table 3 | Redis last supra-threshold time (0.6 / 0.7 / at least 179.8 s) | R1 | `scripts/surface_threshold.py` over `data/crossdomain/` |
| §VII-B, Table 3 | Dockerd presence rates 8 / 20 / 57 / 100% | R3 | read back from `revision_numbers.json` (`R2.11`) by `scripts/surface_threshold.py` |
| §VII-C | Dockerd presence Wilson intervals | R4 | arithmetic from the rates above; no released script emits them |
| §VII-C, Fig. 2a | Per-hour rates 1346 / 1234 / 1345; rho = -0.13 and its bootstrap intervals | R2 | `scripts/regen_ovs_figs.py` writes `fig2_sparse_cascade_per_rep.csv`; the coefficient and the intervals ship in `stats_summary.json` from `scripts/stats_tests.py` |
| §VII-C | Spearman on post-action excess vs surface (rho = 0.09) | R1 | `scripts/surface_excess_spearman.py` |
| §VII-C | Conditional magnitude rho = 0.02, p = 0.94, n = 20 | R1 | `scripts/placebo_control.py` (`surface_correlation_on_step`) |
| §VII-C | Cascade-present cosine 0.89; 3.12x; CI [2.67, 3.63] | R1 | `scripts/signature_cascade_present.py` |
| §VII-C | Within-daemon, cascade-present: Dockerd 2.313x; OvS 1.01x; Redis not estimable | R1 | `scripts/signature_cascade_present.py` |
| §VII-C | Full-corpus within-daemon: Redis 1.85x, Dockerd 2.23x, OvS 1.00x | R1 | `scripts/reanalysis_r2.py` (`daemon_blocked_permutation`) |
| §VII-C | Corpus-wide 2.37x, ANOSIM R = 0.53, PERMANOVA F = 17.8 | R1 | `scripts/signature_replevel_perm.py` |
| §VII-C, Fig. 2b | Per-scenario cosines 0.85 / 0.60 / 0.94 with CIs | R1 | `scripts/within_scenario_ci.py` |
| §VII-C | OvS no-action control 1.02x vs 1.00x | R1 | `scripts/signature_cascade_present.py` (`ovs_no_action_control`) |
| §VII-D, Table 4 | Redis and Dockerd peak, baseline, amplification | R2 | `gen_crossdomain.py` writes `crossdomain_summary.csv` |
| §VII-D, Table 4 | Peak bootstrap intervals | R4 | recomputable in principle from `data/crossdomain/`; no released script emits them |
| §VII-D | Dockerd repetition accounting 16/12, 16/10, 17/7, 17/7 | R1 | `scripts/exclusion_accounting.py` |
| §VII-D | n = 3 pilot FLUSHDB amplification 10.4x | R4 | historical pilot corpus, not part of this package |
| §VII-E, Fig. 4 | Observer CPU, RSS, dump latency, cadence, throughput | R3 | recorded by `exp_overhead/`; released as summary |
| §VII-E | Storage footprint and capture-window figures | R3 | recorded at collection |
| §VII-F, Fig. 5 | Default-GC amplification 10.9 / 12.6 / 11.4x, rho = 0.09 | R2 | `scripts/regen_figs_data.py`; underlying corpus not redistributed |
| §VII-F | Default-GC per-action accounting and launch timing | R3 | recorded at collection |
| §VII-G, Fig. 6 | Cross-environment similarity and 4.0x vs 2.0x | R2 | `scripts/reanalysis_r2.py` (`cross_environment_robustness`) over released summaries |
| §VII-H | Overlap arm, 24-cell sweep, between-spacing p = 0.47 | R1 | `scripts/overlap_analysis.py` |
| §VII-I | Label migration 14,429 events, 84% | R2 | `scripts/ablation_migration.py` needs `--snapshots`; the result ships in `ablation_migration.json` |
| §VII-J | Oracle 155/102/90/12; recall 100%, precision 63% / 75% | R1 | `exp_dbcascade/dbcascade_labeler_eval.py` writing `exp_dbcascade/dbcascade_result.json`; mirrored in `reanalysis_r2.json` |
| §VI | Labeler grid, 60 cells, recall 0.23 / 0.54 / 1.00 | R2 | `scripts/grid_wcd.py` needs `--snapshots`; the grid ships in `grid_corrected_rich.json` |
| §VI | Threshold sensitivity 29/30 for max, 30/30 for the other three | R2 | `scripts/compare_thresholds.py` exits without the raw corpus; the values ship in `threshold_comparison.csv` |
| §IX | OvS capture ceiling: 33 pages, censored feature values, about 32% of iterations | R1 | readable from the `max_page` and `n_active` columns of `data/processed/ovs_recollection_aggregates/`; `scripts/build_ovs_aggregates.py` rebuilds them but needs the raw captures |
| §IX | Observer-effect cadence check at 0.25 s | R3 | recorded at collection; no 0.25 s corpus is redistributed |
| — | Raw OvS process-memory dumps | R4 | not redistributed |
| — | Per-repetition OvS collection schedules | R4 | produced by the SDN harness, not in package |
| — | SDN testbed harness | R4 | not part of this package |

`threshold_comparison.csv` also carries two treatments that recover no induced
repetition (`T4_z3` and `T5_z5`, 0/30). The manuscript compares the four descriptive
treatments and does not report those two.

What the grid in §VI evaluates per cell is label stability and induced-cascade
retention. It does not evaluate a per-cell induced false-association rate; the
false-association reading is the shifted-anchor control, which is a single descriptive
matched-window contrast rather than a per-cell quantity.
