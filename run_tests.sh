#!/usr/bin/env bash
# Runs every test suite. Integration and end-to-end suites need RabbitMQ with the
# 'maltashop_test' vhost (see scripts/setup_rabbitmq.sh) and are SKIPPED, loudly, if it is unreachable.
# Usage: ./run_tests.sh [extra pytest args, e.g. -v]
cd "$(dirname "$0")"
source .venv/bin/activate
set -e
for suite in common order_service notification_service inventory_service payment_service; do
  echo; echo "=================== $suite ==================="
  (cd "$suite" && python -m pytest -q -rs "$@")
done
echo; echo "=================== end-to-end (4 real services + real RabbitMQ) ==================="
python -m pytest tests_e2e -q -rs "$@"
