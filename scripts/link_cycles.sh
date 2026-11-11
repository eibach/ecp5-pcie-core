#!/bin/bash
# Copyright (c) 2026 Dirk Eibach
# SPDX-License-Identifier: BSD-2-Clause
# Link cycles on the host under test (run as root ON THE HOST):
#   driver unload, card removal, Retrain Link on the root port, rescan, driver load, scratch_test and a 3 s DMA test,
#   with the kernel log checked each time.
# Usage: link_cycles.sh [cycles]   (default 20)
# Needs in $DIR (default /tmp): litepcie.ko, litepcie_util. RP is the root port, DEV the card (override with the environment).
N=${1:-20}; RP=${RP:-0000:00:02.1}; DEV=${DEV:-0000:01:00.0}; DIR=${DIR:-/tmp}; ok=0
cd "$DIR"
for i in $(seq 1 $N); do
    t0=$(date +%s.%N); err=""
    dmesg -c > /dev/null 2>&1
    rmmod litepcie 2>/dev/null
    echo 1 > /sys/bus/pci/devices/$DEV/remove 2>/dev/null
    sleep 0.5
    lc=$(setpci -s $RP CAP_EXP+0x10.w); setpci -s $RP CAP_EXP+0x10.w=$(printf '%04x' $((0x$lc | 0x20)))
    # Wait for the link (DLActive, LnkSta bit 13), up to 5 s.
    up=0; for w in $(seq 1 50); do
        ls=$(setpci -s $RP CAP_EXP+0x12.w); if (( (0x$ls & 0x2000) != 0 && (0x$ls & 0x800) == 0 )); then up=1; break; fi; sleep 0.1; done
    [ $up = 1 ] || err="link did not come back (LnkSta 0x$ls)"
    t1=$(date +%s.%N)
    echo 1 > /sys/bus/pci/rescan; sleep 0.5
    [ -e /sys/bus/pci/devices/$DEV ] || err="$err; card not enumerated"
    if [ -z "$err" ]; then
        insmod "$DIR/litepcie.ko" 2>/dev/null; sleep 0.5
        [ -e /dev/litepcie0 ] || err="$err; no /dev/litepcie0"
    fi
    if [ -z "$err" ]; then
        sc=$(./litepcie_util scratch_test 2>&1 | tr '\n' ' ')
        dm=$(timeout 30 ./litepcie_util dma_test -w 32 -t 3 2>&1 | grep -E '^ +[0-9]' | tail -1)
        errs=$(echo "$dm" | awk '{print $NF}')
        echo "$sc" | grep -q -i -E "error|fail" && err="$err; scratch: $sc"
        [ "$errs" = "0" ] || err="$err; dma errors '$errs'"
    fi
    bad=$(dmesg | grep -i -E "aer|fatal|uncorrect|timeout|Bad TLP|nonfatal" | head -2 | tr '\n' '|')
    [ -n "$bad" ] && err="$err; dmesg: $bad"
    if [ -z "$err" ]; then ok=$((ok+1)); printf 'cycle %2d OK   retrain+link %.2fs  dma "%s"\n' $i $(awk -v a=$t1 -v b=$t0 'BEGIN {print a - b}') "$(echo $dm | tr -s ' ')"; else printf 'cycle %2d FAIL %s\n' $i "$err"; fi
done
echo "RESULT $ok/$N cycles OK"
