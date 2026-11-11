#!/usr/bin/env bash
# Copyright (c) 2026 Dirk Eibach
# SPDX-License-Identifier: BSD-2-Clause
# Host-side PCIe link report for the card under test (run ON THE HOST under test).
# Read-only by default. Writes only with the explicit options below (they need root and change host state):
#   --rescan          echo 1 > /sys/bus/pci/rescan
#   --retrain BDF     set Retrain Link (Link Control bit 5) on the given root port/bridge (setpci)
# Usage: scripts/host_pcie_report.sh [--id 1204:ec11] [--rescan] [--retrain 0000:00:1c.0] > report.txt
set -u
id="1204:ec11"   # The ECP5 soft PHY (vendor 1204, device ec11).
rescan=0
retrain=""
while [ $# -gt 0 ]; do
    case "$1" in
        --id) id="$2"; shift 2;;
        --rescan) rescan=1; shift;;
        --retrain) retrain="$2"; shift 2;;
        -h|--help) sed -n 2,8p "$0"; exit 0;;
        *) echo "unknown option $1" >&2; exit 2;;
    esac
done
sec() { printf '\n===== %s =====\n' "$*"; }

sec "date / kernel / cmdline"
date -Is; uname -srv; cat /proc/cmdline
grep -o 'pcie_aspm=[a-z]*' /proc/cmdline >/dev/null || echo "NOTE: pcie_aspm=off is not on the command line (the bring-up procedure asks for it)."

if [ "$rescan" = 1 ]; then
    sec "rescan"; echo 1 > /sys/bus/pci/rescan && echo "rescan done"; sleep 1
fi
if [ -n "$retrain" ]; then
    sec "retrain link on $retrain (Link Control bit 5)"
    setpci -s "$retrain" CAP_EXP+0x10.w=0x0020:0x0020 && echo "retrain requested"; sleep 1
fi

sec "lspci -tv"; lspci -tv
dev=$(lspci -D -d "$id" | awk '{print $1}' | head -1)
if [ -z "$dev" ]; then
    sec "device $id NOT enumerated"
    echo "No device with id $id. Root ports (link status below shows whether a link was ever trained):"
else
    sec "device $dev"; lspci -vvv -s "$dev"
    sec "device config space (first 64 bytes)"; lspci -xxx -s "$dev" | head -6
    echo "BAR0 resource: /sys/bus/pci/devices/$dev/resource0 ($(stat -c %s /sys/bus/pci/devices/$dev/resource0 2>/dev/null) bytes)"
fi

# Upstream bridge/root port of the device (or every root port when the device is missing).
if [ -n "$dev" ]; then
    parent=$(basename "$(dirname "$(readlink -f /sys/bus/pci/devices/$dev)")")
    ports="$parent"
else
    ports=$(lspci -D | awk '/PCI bridge|Root Port/ {print $1}')
fi
for p in $ports; do
    sec "port $p"; lspci -vvv -s "$p" | grep -E "^[0-9a-f:.]+ |LnkCap:|LnkCtl:|LnkSta:|LnkCtl2:|LnkSta2:|Slot|SltSta|DevSta|UESta|CESta|AERCap|Kernel driver" 
    for f in aer_dev_correctable aer_dev_fatal aer_dev_nonfatal; do
        [ -r /sys/bus/pci/devices/$p/$f ] && { echo "-- $f"; cat /sys/bus/pci/devices/$p/$f; }
    done
done

sec "dmesg: PCI / AER / pcieport"
dmesg 2>/dev/null | grep -Ei "pci|aer|pcieport|link (up|down)|hotplug" | tail -60 || echo "(dmesg needs root)"
