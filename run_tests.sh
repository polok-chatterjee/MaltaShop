#!/usr/bin/env bash
# Runs the test suite of each service in isolation (each has its own app package).
set -e
for svc in order_service notification_service; do
  echo "=== $svc ==="
  (cd "$svc" && python -m pytest -q)
done
