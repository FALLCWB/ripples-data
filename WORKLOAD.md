# Workload specification

Exact per-scenario actions and common measurement parameters, for replication.

## Common parameters
- **Observer cadence:** 0.5 s per-page memory dump.
- **Event threshold:** 95th percentile of the pre-action (warmup) signal, per repetition.
- **Warmup / aftermath:** stated per experiment (cross-domain arms use 180 s / 180 s; the overhead runs use a 120 s dump window; the robustness arm uses 120 s / 200 s).

## Open vSwitch (case study)
Flow-table actions on the switch bridge `br0`:
- **single-rule** (surface 1): `ovs-ofctl add-flow br0 "in_port=999,priority=12345,actions=drop"`
- **burst** (surface 21): loop over `in_port` in [200, 220]: `ovs-ofctl add-flow br0 "in_port=$i,priority=11000,actions=drop"`
- **flush** (surface ~200): `ovs-ofctl del-flows br0`
- **overlapping**: two induced actions 30 s and 150 s apart, both inside the aftermath window.

Background (routine-condition) scenarios used for the labeler decomposition (Table 3), not for the magnitude/signature actions:
- **rule-install**: scripted periodic flow-rule installs on `br0` across the run (the routine administrative condition), under the frozen collection protocol (warmup 300 s, settling 120 s; installs on a fixed cadence of roughly one every ~110 s per repetition).
- **sustained-traffic**: continuous packet traffic forwarded through `br0` for the run duration (the routine load condition).

The exact per-repetition action schedule (action names and timestamps) is recorded in each repetition's `events.json` in the raw OvS recollection; that raw capture is too large to host online (1.3 GB), so the per-rep event logs are not redistributed, but the released per-iteration aggregates and labels reproduce every reported OvS value.

## Redis
Server: `redis-server --save '' --appendonly no` (snapshotting and AOF disabled).
- **SET** (1 key): `redis-cli SET k1 v1`
- **MSET** (100 keys): `redis-cli MSET k0 v0 ... k99 v99`
- **FLUSHDB**: `redis-cli FLUSHDB`
- Robustness arm: Redis 6.2 on Debian (`redis:6-bookworm`, glibc) and Redis 7.4 on Alpine (`redis:7-alpine`, musl), on two hosts.

## Dockerd
Docker engine with the Go runtime configured for measurement (`GOGC=off` plus a manual GC forced before warmup and again immediately before the action, via the pprof endpoint) in the main arms, and with the default collector (`GOGC=100`, no manual trigger) in the default-GC arm.
- **readback** (state read, no container): `docker version`
- **N containers** (N in {1, 10, 50}): N successive `docker run -d --rm alpine:latest sleep 60`
- Containers auto-remove (`--rm`); each launch is bounded at 30 s.
- **Exclusion criterion (pre-registered):** a repetition is excluded if the action does not complete within the aftermath window (for the N-container actions, if a launch exceeds the 30 s bound). Attempted/kept/excluded counts are reported per action.

## Reproducing the reported numbers
The processed per-repetition outputs are in `data/processed/`. The revision analyses are reproduced by the scripts in `scripts/` (repetition-level statistics, feature signature, parameter grid, ablation) and `exp_overhead/` (observer cost). Raw process-memory dumps are multi-gigabyte and contain live memory, so they are not redistributed; the derived per-iteration streams that every figure and table are built from are included here.
