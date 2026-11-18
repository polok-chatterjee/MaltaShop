#!/usr/bin/env bash
# Stop or (re)start ONE service.   ./scripts/service.sh stop notification_service
#                                  SIMULATE_TRANSIENT_FAILURES=2 ./scripts/service.sh start notification_service
cd "$(dirname "$0")/.."
source .venv/bin/activate
declare -A PORTS=( [order_service]=8001 [notification_service]=8002 [inventory_service]=8003 [payment_service]=8004 )
action=$1; svc=$2
case $action in
  stop)  [ -f logs/$svc.pid ] && kill "$(cat logs/$svc.pid)" 2>/dev/null; rm -f logs/$svc.pid; sleep 1; echo "$svc stopped" ;;
  start) ( cd "$svc"; nohup python -m uvicorn app.main:app --port "${PORTS[$svc]}" >> "../logs/$svc.log" 2>&1 < /dev/null & echo $! > "../logs/$svc.pid" )
         for i in $(seq 1 40); do curl -s localhost:${PORTS[$svc]}/health | grep -q '"consumer_connected":true' && { echo "$svc started"; exit 0; }; sleep 0.5; done
         echo "$svc did not become ready"; exit 1 ;;
  *) echo "usage: $0 stop|start <service_dir_name>"; exit 1 ;;
esac
