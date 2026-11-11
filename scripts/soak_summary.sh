#!/bin/bash
# Summary of a litepcie_util dma_test soak (run ON THE HOST once the test has ended; do not touch the host while it runs).
# Usage: soak_summary.sh <dma_test log>      (start the soak with: setsid nohup ./litepcie_util dma_test -w 32 -t 900 > log 2>&1 < /dev/null &)
log=${1:?usage: soak_summary.sh <dma_test log>}
echo "== dma_test samples (complete lines only; a sample is one report line)"
grep -E '^ +[0-9.]+\s+[0-9]+\s+[0-9]+\s+[0-9]+\s+[0-9]+\s*$' "$log" | awk '
    { n++; s += $1; if ($5 > 0) { bad++; print "ERROR SAMPLE: " $0 } d = $4; if (d > dmax) dmax = d; if (dmin == "" || d < dmin) dmin = d; last = $0 }
    END { print "samples", n, "mean Gbit/s", s/n, "samples with errors", bad+0, "in-flight diff min/max", dmin, dmax; print "last:", last }'
echo "== kernel log"
echo "driver 'too late' events (lost buffers: host not servicing the ring): $(dmesg | grep -c 'too late')"
echo "AER/fatal/timeout lines: $(dmesg | grep -c -i -E 'aer|fatal|uncorr|bad tlp|timeout')"
echo "== error status of the card and of its root port"
lspci -vv -s "${DEV:-01:00.0}" | grep DevSta
lspci -vv -s "${RP:-00:02.1}" | grep -E 'DevSta|LnkSta:'
echo "== interrupts counted by the driver"; grep litepcie /proc/interrupts | cut -c1-80
echo "(the errors of a slow host are whole buffers of 2048 words: errors/2048 is an integer; compare with the 'too late' lines)"
