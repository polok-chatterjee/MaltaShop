#!/usr/bin/env bash
# Starts all four services in the background. Logs: logs/<service>.log, PIDs: logs/<service>.pid
cd "$(dirname "$0")/.."
source .venv/bin/activate
mkdir -p logs
declare -A PORTS=( [order_service]=8001 [notification_service]=8002 [inventory_service]=8003 [payment_service]=8004 )
for svc in order_service notification_service inventory_service payment_service; do
  if [ -f logs/$svc.pid ] && kill -0 "$(cat logs/$svc.pid)" 2>/dev/null; then echo "$svc already running"; continue; fi
  # simple background command (no wrapper subshell holding the terminal open); stdin closed
  ( cd "$svc"; nohup python -m uvicorn app.main:app --port "${PORTS[$svc]}" >> "../logs/$svc.log" 2>&1 < /dev/null & echo $! > "../logs/$svc.pid" )
done
for svc in order_service notification_service inventory_service payment_service; do
  for i in $(seq 1 40); do
    if curl -s localhost:${PORTS[$svc]}/health | grep -q '"consumer_connected":true'; then echo "$svc up on :${PORTS[$svc]}"; break; fi
    sleep 0.5
    [ $i -eq 40 ] && echo "$svc FAILED to become ready - see logs/$svc.log"
  done
done
