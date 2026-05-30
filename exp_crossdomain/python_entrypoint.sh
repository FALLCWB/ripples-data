#!/bin/sh
# Start workload in background
python3 /opt/python_workload.py &
WORKLOAD_PID=$!
sleep 1
# Tell the memdump runner about the workload pid and action timing
export TARGET_PROCESS=python3
export WORKLOAD_PID
exec python3 /opt/memdump_runner.py
