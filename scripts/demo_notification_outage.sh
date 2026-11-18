#!/usr/bin/env bash
# Proves the Order Service does not wait for notification processing:
# stop Notification Service, place an order, see the event wait in RabbitMQ, restart, see it consumed.
cd "$(dirname "$0")/.."
source .venv/bin/activate
CORR="demo-outage-$RANDOM"
echo "### 1. Stop the Notification Service"
./scripts/service.sh stop notification_service
echo; echo "### 2. Place an order while it is DOWN (correlation id $CORR)"
curl -s -o /tmp/outage_order.json -w "HTTP %{http_code} in %{time_total}s\n" -X POST localhost:8001/orders \
  -H 'Content-Type: application/json' -H "X-Correlation-ID: $CORR" \
  -d '{"customer_id":"C001","items":[{"product_id":"P001","quantity":1}]}'
cat /tmp/outage_order.json; echo
OID=$(python -c "import json;print(json.load(open('/tmp/outage_order.json'))['order_id'])")
sleep 2
echo; echo "### 3. The event is waiting in RabbitMQ (notification.order_events), not lost"
python tools/demo.py dlq | grep -E "^notification.order_events "
echo; echo "### 4. Meanwhile the saga continues without Notification Service:"
curl -s localhost:8001/orders/$OID | python -c "import sys,json;o=json.load(sys.stdin);print('status=%s saga_state=%s'%(o['status'],o['saga_state']))"
echo; echo "### 5. Restart the Notification Service"
./scripts/service.sh start notification_service
sleep 2
echo; echo "### 6. Queued events were consumed after restart; notifications for $OID:"
curl -s "localhost:8002/notifications?order_id=$OID" | python -c "
import sys,json
for n in json.load(sys.stdin): print('  status=%s correlation_id=%s message=%r'%(n['status'],n['correlation_id'],n['message']))"
python tools/demo.py dlq | grep -E "^notification.order_events "
echo; python tools/trace.py "$CORR" | grep -E "notification|published to broker  .*OrderCreated|order created|request completed|lines"
