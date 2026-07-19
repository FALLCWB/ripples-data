#!/usr/bin/env python3
"""Enrichment service: the observed stateful software (real Python app + PostgreSQL).

Two kinds of external action are delivered by the runner, so the labeler must
DISCRIMINATE rather than blanket everything as one category:

  - INGEST (SIGUSR1): the induced action. The service reacts with a delayed,
    multi-stage cascade -- fetch a temperature from the (mock) weather API
    (real request latency), then stage enriched writes to Postgres and update
    the in-memory cache/aggregate. Each reactive write is logged as the actor's
    own audit entry. Ground truth: every memory event of this cascade is the
    ingest's reaction -> Induced-cascade.

  - ADMIN (SIGUSR2): an independent, direct operator action (a scripted bulk
    in-place update of a resident buffer). It is NOT a reaction to the ingest;
    it is logged as an independent scripted action. Ground truth: its memory
    event is a direct effect of an independent action -> Direct-anchor.

Signal handlers only enqueue (flag-and-drain); the blocking work runs in the
main loop under try/except, so a failed ingest is recorded as a sentinel and
never silently counted as a good rep, and no signal work re-enters.
"""
import gc
import json
import os
import queue
import signal
import sys
import time
import urllib.request

import psycopg2

gc.disable()

MOCK_URL = os.environ.get("MOCK_WEATHER_URL", "http://127.0.0.1:8099/temp")
LAG_S = float(os.environ.get("FETCH_LAG_S", "4"))
STAGES = int(os.environ.get("CASCADE_STAGES", "6"))
STAGE_INTERVAL_S = float(os.environ.get("STAGE_INTERVAL_S", "1.0"))
AUDIT_PATH = os.environ.get("AUDIT_PATH", "/tmp/dbcascade/audit.jsonl")
PG_DSN = os.environ.get("PG_DSN", "dbname=cascade user=postgres password=pass host=127.0.0.1 port=5433")

cache = {}
aggregate = {"n": 0, "sum": 0.0, "hist": [0] * 256}
_kept = []
resident = bytearray(4 * 1024 * 1024)  # 4 MiB resident buffer, mutated in place (observable)

os.makedirs(os.path.dirname(AUDIT_PATH), exist_ok=True)
audit = open(AUDIT_PATH, "a", buffering=1)


def log_audit(kind, role, action_id):
    # role in {"induced", "reactive", "legit", "failed"}; induced/legit are actions,
    # reactive entries are the actor logging its own cascade writes.
    audit.write(json.dumps({"ts": time.time(), "kind": kind, "role": role,
                            "action_id": action_id}) + "\n")


conn = psycopg2.connect(PG_DSN)
conn.autocommit = True
cur = conn.cursor()
cur.execute("CREATE TABLE IF NOT EXISTS enriched (id serial PRIMARY KEY, raw_id int, "
            "stage int, temperature double precision, payload double precision[], "
            "computed_at double precision)")

for _ in range(500):
    _kept.append(bytes(2048))

q = queue.Queue()
action_id = 0


def on_ingest(*_):
    q.put("ingest")   # minimal: just enqueue, no work in the handler


def on_admin(*_):
    q.put("admin")


signal.signal(signal.SIGUSR1, on_ingest)
signal.signal(signal.SIGUSR2, on_admin)


def ensure_conn():
    global conn, cur
    try:
        cur.execute("SELECT 1")
    except Exception:                                       # dropped/broken connection: reconnect
        conn = psycopg2.connect(PG_DSN)
        conn.autocommit = True
        cur = conn.cursor()


def do_ingest():
    global action_id
    action_id += 1
    aid = action_id
    cache.clear()                                           # bound per-action memory growth (no cross-action drift)
    ensure_conn()                                           # survive a transient DB blip instead of failing all later reps
    log_audit("ingest_raw", "induced", aid)                 # THE INDUCED ACTION
    temp = 15.0 + (aid % 20)                                 # deterministic
    with urllib.request.urlopen(f"{MOCK_URL}?lag={LAG_S}", timeout=LAG_S + 30) as r:
        temp = float(json.loads(r.read()).get("temp", temp))
    for stage in range(STAGES):                             # staged reaction
        payload = [temp + i * 0.01 for i in range(4000)]
        cur.execute("INSERT INTO enriched (raw_id, stage, temperature, payload, computed_at) "
                    "VALUES (%s,%s,%s,%s,%s)", (aid, stage, temp, payload[:64], time.time()))
        cache[(aid, stage)] = payload
        aggregate["n"] += 1
        aggregate["sum"] += temp
        aggregate["hist"][int(temp) % 256] += 1
        # in-place churn so the cascade is observable (address-stable mutations)
        base = (stage * 131072) % (len(resident) - 65536)
        resident[base:base + 65536] = bytes((stage + i) & 0xFF for i in range(65536))
        log_audit("enriched_write", "reactive", aid)        # actor logs its own reaction
        time.sleep(STAGE_INTERVAL_S)
    sys.stdout.write(f"ingest {aid} done\n")
    sys.stdout.flush()


def do_admin():
    global action_id
    action_id += 1
    aid = action_id
    # An independent, direct operator action: a single brief in-place update whose
    # memory footprint is concentrated at the moment it is logged (like a real
    # recorded direct action, unlike the multi-stage reaction). Written to the TOP
    # 256 KiB of the resident buffer, a region DISJOINT from every ingest stage
    # (stages write low offsets, base = (stage*131072) % (len-65536)), so the two
    # actions are separable in space as well as time. Logged right at the write so
    # the audit entry coincides with the memory event it produces.
    top = len(resident) - 262144
    resident[top:] = bytes((aid * 7 + i) & 0xFF for i in range(262144))  # ~256 KiB, one shot, disjoint region
    aggregate["hist"][aid % 256] += 100
    log_audit("admin_update", "legit", aid)                 # INDEPENDENT DIRECT ACTION
    sys.stdout.write(f"admin {aid} done\n")
    sys.stdout.flush()


sys.stdout.write(f"workload ready pid={os.getpid()}\n")
sys.stdout.flush()

while True:
    try:
        item = q.get(timeout=1.0)
    except queue.Empty:
        continue
    try:
        if item == "ingest":
            do_ingest()
        elif item == "admin":
            do_admin()
    except Exception as e:  # a failed action is recorded, never silently counted good
        log_audit(f"action_failed:{item}", "failed", action_id)
        sys.stdout.write(f"ACTION_FAILED {item}: {e}\n")
        sys.stdout.flush()
