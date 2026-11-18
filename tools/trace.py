#!/usr/bin/env python3
"""Show every log line (from all services) that carries a given correlation ID, in time order.

    python tools/trace.py <correlation-id> [logs-dir]
"""
import json
import sys
from pathlib import Path

corr = sys.argv[1]
logdir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent / "logs"
SKIP = {"timestamp", "level", "service", "logger", "correlation_id", "message"}
rows = []
for f in sorted(logdir.glob("*.log")):
    for line in f.read_text(errors="replace").splitlines():
        if line.startswith("{"):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("correlation_id") == corr:
                rows.append(r)
rows.sort(key=lambda r: r["timestamp"])
for r in rows:
    extra = " ".join(f"{k}={v}" for k, v in r.items() if k not in SKIP and k not in ("duration_ms",))
    print(f'{r["timestamp"][11:23]}  {r["service"]:<21} {r["level"]:<7} {r["message"]}  {extra}')
print(f"\n{len(rows)} log lines for correlation ID {corr} across {len({r['service'] for r in rows})} services")
