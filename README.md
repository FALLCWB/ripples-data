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
gen_figures.py   regenerates Fig. 2, 5, 6 from data/processed
gen_crossdomain.py regenerates the cross-domain figure (OvS, Redis, Dockerd panels)
```

Raw Open vSwitch memory snapshots are too large to host online (1.3 GB per recollection). The processed outputs in `data/processed/` contain every value used in the paper text and figures, including the per-iteration aggregates (`ovs_recollection_aggregates/`) and the Induced-first labels (`labels_corrected_{sparse,rich}`) from the frozen-protocol recollection. `scripts/regen_ovs_figs.py` regenerates the scenario decomposition (Table 3) and Figs 2, 5, and 6 from those processed outputs alone, under Algorithm 1's Induced-first order and the 95th-percentile warmup threshold; `scripts/regen_figs_data.py` holds the snapshot-to-features helpers.

Some released identifiers (`D_attack_flush`, `inject_attack`, `*_attack_*`) are legacy names predating the observational framing and carry no attack semantics; the study is purely observational, and scenarios D/E/F are induced administrative actions.

## Revision (2026) additions

Added for the revised submission:

**Scripts** (`scripts/`, `exp_overhead/`, `gen_figures_r1.py`):
- `signature_replevel_perm.py` — repetition-level signature-separation test (ANOSIM/PERMANOVA label permutation + bootstrap CI), replacing the pair-level test.
- `feature_signature_replevel.py` — within-repetition paired feature-signature test.
- `revision_numbers.py` — recomputes and persists the revision numbers (default-GC dissociation, calibrated ripple-presence, readback amplification, shifted-anchor control, robustness signature) into `data/processed/revision_numbers.json`.
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

### `data/processed/fig2_sparse_attack_cascade_per_rep.csv`
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
Aggregates of the pairwise table: `within_mean = 0.735`, `across_mean = 0.310`, `separation_ratio = 2.37`. These are the signature-reproducibility numbers quoted in the results section.

### `data/processed/stats_summary.json`
Wilson intervals and per-scenario rates used in the results section. (The earlier pooled Mann–Whitney values are retained here for provenance; the paper reports repetition-level tests instead, computed by `scripts/signature_replevel_perm.py`.)

### `data/processed/crossdomain_summary.csv`
Per-(system, action) amplification table for the Redis and Dockerd replication.

| column | description |
|---|---|
| `system` | `Redis`, `Dockerd` (the paper's cross-domain analysis; raw `nginx` runs exist under `crossdomain/` but were not summarized or reported) |
| `surface` | action-surface bin (`small`, `medium`, `large`, `readback`, ...) |
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
| within $0.73$ vs across $0.31$, sep $2.4\times$ | `signature_summary.json` | direct read |
| Spearman $\rho = -0.13$ on cascade rate vs surface | `fig2_sparse_attack_cascade_per_rep.csv` | Spearman of `per_hour_rate` vs surface (1/21/200) over 30 reps |
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

> F. Lemos et al. (2026). *Action Ripples in Memory — Dataset and Reproduction Scripts* (v2.1.10). Zenodo. https://doi.org/10.5281/zenodo.20465278

BibTeX:

```bibtex
@dataset{lemos2026ripplesdata,
  author       = {Lemos, Filipe Augusto da Luz and others},
  title        = {{Action Ripples in Memory --- Dataset and Reproduction Scripts}},
  month        = may,
  year         = 2026,
  publisher    = {Zenodo},
  version      = {v2.1.10},
  doi          = {10.5281/zenodo.20465278},
  url          = {https://doi.org/10.5281/zenodo.20465278}
}
```

## License

Data and code are released under **CC BY 4.0**. See `LICENSE`. Attribution should reference the IEEE Access paper above.
