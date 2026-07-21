# Action Ripples in Memory — Dataset

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20465278.svg)](https://doi.org/10.5281/zenodo.20465278)

Data and reproduction scripts for the IEEE Access submission **"Action Ripples in Memory: An Observational Characterization of Memory Cascades after External Actions on Stateful Software"**.

This repository contains the experimental data, processed feature tables, and Python scripts needed to regenerate every figure and statistical claim in the paper.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python gen_figures.py
python gen_crossdomain.py
```

The figures are written next to the scripts (`fig2_surface.png`, `fig5_temporal.png`, `fig6_features.png`, `fig_crossdomain.png`).

## Directory layout

```
data/
  processed/   tidy CSVs and JSON summaries consumed by the figure scripts
  crossdomain/ raw experiment logs from the Redis and Dockerd replication runs
               (also present: nginx and a python-dict workload, collected raw but
               not summarized or reported in the paper)
exp_crossdomain/ capture-side scripts used to produce the crossdomain/ logs
scripts/         data-processing pipeline that produces data/processed from raw captures
gen_figures.py   regenerates the surface (Fig. 2), temporal (Fig. 4), and feature (Fig. 5) figures from data/processed
gen_crossdomain.py regenerates the cross-domain figure (OvS, Redis, Dockerd panels)
```

Raw Open vSwitch memory snapshots are too large to host online (1.3 GB per recollection). The processed outputs in `data/processed/` contain every value used in the paper text and figures, including the per-iteration aggregates (`ovs_recollection_aggregates/`) and the Induced-first labels (`labels_corrected_{sparse,rich}`) from the frozen-protocol recollection. `scripts/regen_ovs_figs.py` regenerates the scenario decomposition (Table 3) and the surface figure (Fig. 2) from those processed outputs alone, under Algorithm 1's Induced-first order and the 95th-percentile warmup threshold; `scripts/regen_figs_data.py` holds the snapshot-to-features helpers.

Released identifiers use the observational vocabulary throughout: scenarios are `D_flush`, `E_single_rule`, and `F_burst`, the action marker in the event logs is `inject_action`, and per-scenario rates are reported as ripple *presence*. The loaders still accept the legacy marker `inject_attack`, which appears only in first-collection captures that are not redistributed; it predates the observational framing and carries no attack semantics. Scenarios D/E/F are induced administrative actions and the study is purely observational.

## Revision (2026) additions

Added for the revised submission:

**Scripts** (`scripts/`, `exp_overhead/`, `gen_figures_r1.py`):
- `signature_replevel_perm.py` — repetition-level signature-separation test (ANOSIM/PERMANOVA label permutation + bootstrap CI), replacing the pair-level test.
- `feature_signature_replevel.py` — within-repetition paired feature-signature test (runs from the released aggregates; `--snapshots` selects the raw capture instead).
- `revision_numbers.py` — recomputes and persists the revision numbers (default-GC dissociation, calibrated ripple-presence, readback amplification, shifted-anchor control, robustness signature) into `data/processed/revision_numbers.json`.
- `build_ovs_aggregates.py` — reduces the raw OvS capture to the released per-iteration aggregates (needs the raw snapshots, which are not redistributed; with them it reproduces the shipped files byte for byte).
- `presence_null.py` — count-level null for the presence claim and the calibrated paired-excess criterion.
- `persistence_profile.py` — WITHDRAWN: its comparator window contained the action. Kept for inspection; duration is reported by `lag_profile.py`.
- `overlap_analysis.py` — closely spaced actions (scenarios G and H), paired before/after contrast with solo-flush control and window sweep.
- `lag_profile.py` — lag-resolved action step in disjoint bins, every comparator window ending at or before the action, with the no-action sham arm.
- `signature_cascade_present.py` — signature separation restricted to the cascade-present conditions, plus the OvS no-action-window control.
- `placebo_control.py` — placebo-anchored control for the Open vSwitch corpus: estimates the within-run ramp from non-overlapping pre-action anchors, removes it by a difference in differences, and reports the sham-anchor negative control and the window sweep.
- `surface_threshold.py` — ripple presence and duration against the action ladder across the three daemons (Table 3 of the paper).
- `exclusion_accounting.py` — attempted/kept/excluded repetitions per Dockerd action under GOGC=off.
- `surface_excess_spearman.py` — surface-versus-magnitude test read on excess mass as well as on the event count.
- `within_scenario_ci.py` — per-scenario within-cosine bootstrap CIs (repetition level) used by Fig. 2(b).
- `ablation_migration.py` — labeler priority-order ablation, writes `ablation_migration.json` (needs the raw snapshots).
- `grid_wcd.py` — W/C/Delta parameter grid, writes `grid_corrected_rich.json` (needs the raw snapshots).
- `labeler_v2.py` — the six-category labeler (Algorithm 1) used by the ablation and the grid.
- `gen_figures_r1.py` — regenerates the overhead, default-GC, and robustness figures.
- `exp_overhead/` — the observer-overhead harness (runs the real capture path in paired with/without-observer arms).

**Processed data** (`data/processed/`):
- `revision_numbers.json` — the revision numbers with their sources.
- `signature_replevel_perm.json` — rep-level signature separation (within/across, permutation p, ANOSIM R, PERMANOVA F, bootstrap CI).
- `grid_corrected_rich.json` — the full W/C/Delta parameter-sensitivity grid (60 cells).
- `ablation_migration.json` — the labeler-order ablation (label migration on contested events).
- `overhead_redis_summary.txt`, `overhead_dockerd_summary.txt`, `overhead_ovs_summary.json` — observer cost (Redis, Dockerd, OvS).
- `reanalysis_r2.json` — second-revision re-analyses (`scripts/reanalysis_r2.py`): daemon-blocked action-label permutation (recomputed from the pairwise CSV), paired rich/sparse audit-coverage comparison on the same flush reps, cross-environment same-action signature cosine, and the oracle confusion matrix with precision.

**`WORKLOAD.md`** — exact per-scenario commands and common measurement parameters.

The revision parameter-sensitivity and ablation analyses run on a fresh recollection under a denser audit regime; those raw traces are not redistributed (multi-GB, live memory), but the processed outputs above contain every reported value.

## File schemas

### Levels of reproduction supported

| Level | Supported? |
| --- | --- |
| Regeneration of every figure, table and statistic from the released processed outputs | yes |
| Recomputation from the released per-repetition aggregates (presence, step, lag profile, signature, overlap) | yes |
| Reconstruction from the raw Open vSwitch captures, per-repetition action schedules and audit files | no: 1.3 GB of live process memory, too large to host online |
| Recomputation of the default-GC, presence-null, shifted-anchor and robustness values | no: their input corpora are not redistributed; the computed values ship in `revision_numbers.json` and the scripts skip with an explicit message |
| The SDN lab harness that drives the OvS testbed | not part of this package |

### Audit-log schema

The controller-side audit entries that anchor the labeler are JSON objects with the fields below. The OvS per-repetition audit files are part of the raw capture and are not redistributed; the same schema is used by the released oracle logs in `exp_dbcascade/data/*/audit.jsonl`.

| field | type | meaning |
| --- | --- | --- |
| `ts` | float (epoch seconds) | when the actor recorded the operation |
| `category` | string | actor-side class of the operation (for OvS: controller flow op, statistics poll, notification) |
| `device` / `swid` | string | component the operation targeted |
| `detail` | object | operation-specific payload, not read by the labeler |

The labeler consumes only `ts` and `category`; every other field is carried through for provenance.

### `data/processed/presence_null.json`
Count-level null for the OvS presence claim, plus the paired-excess criterion (29/30 repetitions, Wilcoxon p = 3.2e-6), which is itself superseded in the paper by the placebo-controlled presence of `placebo_control.json`, since the paired contrast is not controlled for the within-run ramp. Produced by `scripts/presence_null.py`.

### `data/processed/overlap_analysis.json`, `overlap_per_rep.csv`
Closely spaced actions (scenarios G and H): paired before/after contrast around the second action, with the solo-flush control at matched elapsed time and a window sweep. Produced by `scripts/overlap_analysis.py`.

### `data/processed/scenario_decomposition.csv`
Per-scenario six-category decomposition (paper Table 3): one row per (scenario, audit mode), reporting the per-repetition mean rate in events/hour over ten repetitions, under the Induced-first labeler of Algorithm 1. Produced by `scripts/regen_ovs_figs.py`.

| column | description |
|---|---|
| `scenario` | scenario name (`Idle`, `Rule installation`, `Sustained traffic`, `Flow-table flush`, `Single-rule insertion`, `Multi-rule burst`) |
| `audit` | `sparse` or `rich` audit coverage |
| `reps` | repetitions aggregated (10) |
| `Direct-anchor` | per-hour rate of events anchored to an audit entry within W |
| `Reactive-cascade` | per-hour rate of post-anchor reactive events |
| `Induced-cascade` | per-hour rate of events in the induced aftermath (Induced-first) |
| `Periodic-gap` | per-hour rate of events in a known periodic gap |
| `Endogenous-unexplained` | per-hour rate of residual unexplained events |
| `Indeterminate` | per-hour rate of events outside any decidable region |

### `data/processed/fig2_sparse_cascade_per_rep.csv`
Per-rep cascade decomposition restricted to scenarios with sparse audit (used in Fig. 2 surface).

### `data/processed/fig5_temporal_signal.csv`
Single representative repetition trace used for the temporal-signature figure.

| column | description |
|---|---|
| `t_rel_s` | seconds relative to the action timestamp |
| `signal` | per-iteration `change_volume_sum` |
| `threshold` | 95th percentile of the pre-action (warmup) signal for that rep |

### `data/processed/fig6_feature_distributions.csv`
Long-form per-feature distributions for the baseline/ripple comparison.

### `data/processed/signature_pairwise_similarity.csv`
Pairwise cosine similarity between per-rep temporal signatures.

| column | description |
|---|---|
| `rep_a`, `rep_b` | rep IDs |
| `scenario_a`, `scenario_b` | scenarios |
| `same_scenario` | boolean — within vs across |
| `cosine_similarity` | cosine over the post-action feature vector |

### `data/processed/signature_summary.json`
Aggregates of the pairwise table: `within_mean = 0.735`, `across_mean = 0.310`, `separation_ratio = 2.37`. These are the corpus-wide trace-repeatability numbers, which the paper reports as secondary; the primary statistic is in `signature_cascade_present.json`.

### `data/processed/stats_summary.json`
Per-scenario ripple presence (10/10 per scenario, 30/30 pooled, with Wilson 95% intervals), the surface-monotonicity Spearman (rho = -0.13, n = 30), the per-scenario bootstrap means and CIs (1345/1346/1234 per hour), and the pooled feature-signature Mann-Whitney. The pooled Mann-Whitney is superseded in the paper by the repetition-level paired test of `scripts/feature_signature_replevel.py` and is kept for continuity with the first submission; the file's own `_note` field says so. The presence figure quoted in the paper is the placebo-controlled reading of `placebo_control.json` and `surface_threshold.json`; both this event-count rate and the paired-excess figure of `presence_null.json` are superseded, and are kept for continuity. Produced by `scripts/stats_tests.py`.

### `data/processed/crossdomain_summary.csv`
Per-(system, action) amplification table for the Redis and Dockerd replication.

| column | description |
|---|---|
| `system` | `Redis`, `Dockerd` (the paper's cross-domain analysis; raw `nginx` runs exist under `crossdomain/` but were not summarized or reported) |
| `surface` | action-surface bin (`small`, `medium`, `large`, `xlarge`; the Dockerd readback is `surface=small`) |
| `action` | concrete command issued |
| `n_reps` | repetitions |
| `baseline_pages` | calibration baseline |
| `peak_pages_mean`, `peak_pages_std` | peak post-action page count |
| `amplification` | peak / baseline |

### `data/crossdomain/*.log`
Raw stdout from each `(system, action, rep)` capture run. Naming pattern `<system>_<action>_rep<N>_<unix_ts>.log`. The processed `crossdomain_summary.csv` is computed from these via `gen_crossdomain.py`.

### `data/processed/threshold_comparison.csv`
Sensitivity of the cascade decomposition to alternative threshold definitions (`T1_max`, `T2_p99`, `T3_p95`, `T4_z3`, `T5_z5`, `T6_mad5`; the paper reports max, 99th percentile, and median + 5 MAD against the 95th-percentile operating point). Supports the threshold-choice robustness claim.

## Regenerating processed tables from raw captures

The full Open vSwitch raw memory snapshots total 1.3 GB per recollection and are too large to host online. The figures and tables are regenerated instead from the released processed outputs (no raw needed):

```bash
python scripts/regen_ovs_figs.py
```

This reproduces `scenario_decomposition.csv` and the `fig2`/`fig5`/`fig6` CSVs from the per-iteration aggregates (`ovs_recollection_aggregates/`) and the Induced-first labels under `data/processed/`, under Algorithm 1's order and the 95th-percentile threshold.

The capture-side instrumentation (per-iteration `change_volume_sum`, `n_changed_pages`, audit log cross-reference) is described in Section III of the paper and the runner scripts under `exp_crossdomain/`.

## Reproducing specific paper numbers

| Paper claim | Source file | Compute |
|---|---|---|
| within $0.84$ vs across $0.50$, sep $1.70\times$ (cascade-present, the paper's primary) | `signature_cascade_present.json` | `scripts/signature_cascade_present.py` |
| within $0.735$ vs across $0.310$, sep $2.37\times$ (corpus-wide, reported as secondary) | `signature_summary.json` | direct read |
| Spearman $\rho = -0.13$ on cascade rate vs surface | `fig2_sparse_cascade_per_rep.csv` | Spearman of `per_hour_rate` vs surface (1/21/200) over 30 reps |
| Presence transition per action across the three daemons | `surface_threshold.json`, `placebo_control.json` | `scripts/surface_threshold.py`; OvS read as the placebo-controlled step |
| Action-attributable step, 1.25x at 30 s windows | `placebo_control.json` | `scripts/placebo_control.py --win 30` |
| Lag profile: duration in disjoint bins, comparators all pre-action | `lag_profile.json` | `scripts/lag_profile.py` |
| Signature restricted to cascade-present conditions, and the OvS no-action control | `signature_cascade_present.json` | `scripts/signature_cascade_present.py` |
| Spearman on post-action excess vs surface | `surface_excess_spearman.json` | `scripts/surface_excess_spearman.py` |
| within-vs-across separation, rep-level permutation $p < 10^{-4}$ (ANOSIM $R=0.53$, PERMANOVA $F=17.8$) | `signature_pairwise_similarity.csv` | `python scripts/signature_replevel_perm.py` |
| predicted cascade present in $30/30$ sparse-audit reps | `stats_summary.json` | direct read |
| Dockerd amplification $4.9 \times$ → $87 \times$ | `crossdomain_summary.csv` | direct read |

## Software environment

- Python 3.12.3
- numpy, pandas, matplotlib, scipy (see `requirements.txt`)
- Open vSwitch 3.3.0 (capture host)
- Redis 7.4 (robustness arm 6.2), Docker 25.x (cross-domain replication); nginx 1.24 (collected raw, not reported)
- Linux 6.x with `/proc/<pid>/pagemap` enabled

The capture pipeline is OS-bound; the analysis pipeline in this repository is platform-independent.

## Citing this dataset

> F. Lemos et al. (2026). *Action Ripples in Memory — Dataset and Reproduction Scripts* (v2.4.0). Zenodo. https://doi.org/10.5281/zenodo.20465278

BibTeX:

```bibtex
@dataset{lemos2026ripplesdata,
  author       = {Lemos, Filipe Augusto da Luz and others},
  title        = {{Action Ripples in Memory --- Dataset and Reproduction Scripts}},
  month        = may,
  year         = 2026,
  publisher    = {Zenodo},
  version      = {v2.4.0},
  doi          = {10.5281/zenodo.20465278},
  url          = {https://doi.org/10.5281/zenodo.20465278}
}
```

## License

Data and code are released under **CC BY 4.0**. See `LICENSE`. Attribution should reference the IEEE Access paper above.
