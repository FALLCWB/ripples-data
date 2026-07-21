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
- **sustained-traffic**: continuous ICMP echo traffic forwarded through `br0` for the run duration (the routine load condition). Generator: `docker exec host1 ping -c 480 -i 1.0 -W 1 10.255.255.2`, issued by the `inject_ping` primitive of the lab harness, which is not part of this package (it drives the SDN testbed rather than the analysis). Rate one echo request per second for 480 s (480 requests per repetition, 480 replies, 0% loss in the released repetitions); payload is the `ping` default of 56 data bytes, that is an 84-byte IPv4 packet on the wire in each direction. The traffic runs host1 to host2 across the monitored bridge, so every packet traverses `ovs-vswitchd`.

The exact per-repetition action schedule (action names and timestamps) is recorded in each repetition's `events.json` in the raw OvS recollection; that raw capture is too large to host online (1.3 GB), so the per-rep event logs are not redistributed, but the released per-iteration aggregates and labels reproduce every reported OvS value.

## Redis
Server: `redis-server --save '' --appendonly no` (snapshotting and AOF disabled).
- **SET** (1 key): `redis-cli SET k1 v1`
- **MSET** (100 keys): `redis-cli MSET k0 v0 ... k99 v99`
- **FLUSHDB**: `redis-cli FLUSHDB`. The measurement container starts a fresh `redis-server` and the arm performs no population step, so this rung runs against an unpopulated keyspace: it differs from SET and MSET in the command's code path, not in the number of keys touched. Database cardinality at action time was not recorded.
- Robustness arm: Redis 6.2 on Debian (`redis:6-bookworm`, glibc) and Redis 7.4 on Alpine (`redis:7-alpine`, musl), on two hosts.

## Dockerd
Docker engine with the Go runtime configured for measurement (`GOGC=off` plus a manual GC forced before warmup and again immediately before the action, via the pprof endpoint) in the main arms, and with the default collector (`GOGC=100`, no manual trigger) in the default-GC arm.
- **readback** (read-only daemon query; no container spawned, no image mounted, no cgroup created): `docker version`
- **N containers** (N in {1, 10, 50}): N successive `docker run -d --rm alpine:latest sleep 60`
- Containers auto-remove (`--rm`); each launch is bounded at 30 s.
- **Exclusion criterion (pre-registered):** a repetition is excluded if the action does not complete within the aftermath window (for the N-container actions, if a launch exceeds the 30 s bound). Attempted/kept/excluded counts are reported per action.

## Image identifiers

Images were pulled by mutable tag at collection time. The identifiers below are the immutable digests and
image IDs of what actually ran, read back from the collection hosts; use these rather than the tags to
reproduce the exact software.

| Role | Tag used | Immutable identifier |
| --- | --- | --- |
| OvS switch container (case study) | `memory-monitoring-sdn-switch1` (locally built) | `sha256:1332808149c1708d161a2a4acd37e904cb4736125bb510dada8915b174a0efb2` |
| Traffic hosts | `memory-monitoring-sdn-host1` / `-host2` (locally built) | `sha256:51be9d2425c40bd8deee24f68f29392d4b1b8615da0c065b8d03d0b990e2e824` / `sha256:8d3c9fc6023f1833f3836e000445a7b3ffbfdf8cc4813ec26007d50b8f412d89` |
| ONOS controller | `onosproject/onos:2.7-latest` | `sha256:ee324bcf56ed01497069143f83f1315fcf634b36f1ac3c589a8c54ccaf354813` |
| Redis 7.4 (main arm) | `redis:7-alpine` | `redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99` |
| Redis measurement image (built on the above) | `echo-redis-par-img` | `sha256:5583b8b844cd0990272ca981e95742a167a29ace64f4a9968a83b0451e23dcae` |
| Redis 6.2 Debian (robustness arm) | built `FROM redis:6-bookworm` | `sha256:b056eb5822dff5ae666664f16527928f151e398caa3517b93db0a337c4b02646` |
| Dockerd measurement image | `echo-dind-par-img` (from `docker:dind`) | `sha256:9c61eb45343d8536866100c2758f061fe9d34f7917c35f9df6d6d24675ed2c27` |
| Dockerd overhead image | `echo-dind-img` | `sha256:bc1b9490cbd5225c332f69594e9c6ac24594c15d031162406103be5ea6089636` |
| `docker:dind` base | `docker:dind` | `docker@sha256:6b9cd914eb9c6b342c040a49a27a5eb3804453bae6ecc90f7ff96133595a95e8` |
| Container payload for the Dockerd actions | `alpine:latest` | `alpine@sha256:5b10f432ef3da1b8d4c7eb6c487f2f5a8f096bc91145e68878dd4a5019afde11` |
| PostgreSQL behind the controlled oracle | `postgres:16-alpine` | `postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777` |

Locally built images carry an image ID rather than a registry digest because they are not pushed to a registry;
their Dockerfiles are in `exp_crossdomain/`, `exp_overhead/`, and the lab harness repository.

## Reproducing the reported numbers
The processed per-repetition outputs are in `data/processed/`. The revision analyses are reproduced by the scripts in `scripts/` (repetition-level statistics, feature signature, parameter grid, ablation) and `exp_overhead/` (observer cost). Raw process-memory dumps are multi-gigabyte and contain live memory, so they are not redistributed; the derived per-iteration streams that every figure and table are built from are included here.
