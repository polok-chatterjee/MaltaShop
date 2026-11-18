#!/usr/bin/env bash
# One-time RabbitMQ preparation (needs sudo). Creates the application vhost and a test vhost.
# The exchanges/queues themselves are declared automatically by the services on start-up.
set -e
for v in maltashop maltashop_test; do
  sudo rabbitmqctl add_vhost "$v" 2>/dev/null || echo "vhost $v already exists"
  sudo rabbitmqctl set_permissions -p "$v" guest ".*" ".*" ".*"
done
echo "RabbitMQ ready. Vhosts:"; sudo rabbitmqctl list_vhosts
