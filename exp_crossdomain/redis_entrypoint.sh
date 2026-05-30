#!/bin/sh
# Start redis-server in background (no snapshotting)
redis-server --save '' --appendonly no --daemonize yes
sleep 1
# Run memdump runner (foreground, will exit after observation)
exec python3 /opt/memdump_runner.py
