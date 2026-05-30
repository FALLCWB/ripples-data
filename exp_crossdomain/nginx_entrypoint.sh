#!/bin/sh
# Start nginx in background as daemon
nginx
sleep 1
exec python3 /opt/memdump_runner.py
