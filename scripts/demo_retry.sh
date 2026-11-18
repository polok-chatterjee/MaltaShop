#!/usr/bin/env bash
# Shows retry-with-backoff: restart Notification Service so its first 2 attempts fail transiently.
cd "$(dirname "$0")/.."
source .venv/bin/activate
echo "### 1. Restart Notification Service with SIMULATE_TRANSIENT_FAILURES=2 (demo knob)"
./scripts/service.sh stop notification_service
SIMULATE_TRANSIENT_FAILURES=2 ./scripts/service.sh start notification_service
echo; echo "### 2. Place an order (its OrderCreated event will fail twice, then succeed)"
CORR="demo-retry-$RANDOM"
curl -s -X POST localhost:8001/orders -H 'Content-Type: application/json' -H "X-Correlation-ID: $CORR" \
  -d '{"customer_id":"C001","items":[{"product_id":"P001","quantity":1}]}' > /dev/null
sleep 4
echo; echo "### 3. Notification Service log lines for $CORR"
python tools/trace.py "$CORR" | grep -E "notification-service|lines"
echo; echo "### 4. Restore normal operation"
./scripts/service.sh stop notification_service
./scripts/service.sh start notification_service
