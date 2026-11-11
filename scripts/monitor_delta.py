#!/usr/bin/env python3
"""Link-layer counters of the card over a test: the difference of two snapshots of the status page's API (32-bit wrap safe).

  curl -s localhost:8765/api/state > before.json   # before starting the soak
  curl -s localhost:8765/api/state > after.json    # once it has ended
  python3 scripts/monitor_delta.py before.json after.json
"""
import json, sys
M = 2**32
b, a = (json.load(open(f)) for f in sys.argv[1:3])
print("receiver counters:", {k: (a["counters"][k] - b["counters"][k]) % M for k in ("rx_ts", "rx_ts_errors", "rx_errors", "rx_overflows")})
d = {k: (a["dll"][k] - b["dll"][k]) % M for k in ("replays", "rx_errors", "link_errors", "tx_tlps", "rx_tlps")}
print("data link layer:", d)
print("DCU:", a["dcu"])
