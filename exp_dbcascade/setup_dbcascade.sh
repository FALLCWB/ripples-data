#!/bin/bash
# Start Postgres (backing store) for the DB-cascade experiment.
set -e
docker rm -f pg-cascade 2>/dev/null || true
docker run -d --name pg-cascade -p 5433:5432 \
  -e POSTGRES_PASSWORD=pass -e POSTGRES_DB=cascade postgres:16-alpine >/dev/null
for i in $(seq 1 40); do
  docker exec pg-cascade pg_isready -U postgres >/dev/null 2>&1 && { echo "postgres ready"; exit 0; }
  sleep 1
done
echo "postgres FAILED to become ready"; exit 1
