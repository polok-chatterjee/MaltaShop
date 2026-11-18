#!/usr/bin/env bash
# Stops services, deletes demo databases and logs, and empties the demo queues.
cd "$(dirname "$0")/.."
./scripts/stop_all.sh
rm -f */*.db logs/*.log
source .venv/bin/activate
python tools/demo.py purge
