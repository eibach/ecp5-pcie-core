#!/usr/bin/env python3
# Copyright (c) 2026 Dirk Eibach
# SPDX-License-Identifier: BSD-2-Clause
"""DCU status of the bring-up design over a litex_server (UARTBone): PLL/CDR lock, loss of signal, init FSM, idle delay.

  litex_server --uart --uart-port /dev/ttyUSB1 --uart-baudrate 115200 &
  python3 scripts/dcu_status.py --csr-csv build/bringup_l75_s1/csr.csv
"""
import argparse
from litex import RemoteClient

STATES = ["RESET-ALL", "WAIT-TX-PLL-LOCK", "RESET-TX-PCS", "WAIT-RX-SIGNAL", "RESET-RX-CDR", "WAIT-RX-CDR-LOCK", "RESET-RX-PCS", "READY"]

ap = argparse.ArgumentParser()
ap.add_argument("--csr-csv", required=True)
ap.add_argument("--host", default="localhost")
ap.add_argument("--port", default=1234, type=int)
args = ap.parse_args()
bus = RemoteClient(csr_csv=args.csr_csv, host=args.host, port=args.port); bus.open()
regs = bus.regs
prefix = next((g + "serdes_" for g in ("pcie_endpoint_phy_", "pcie_phy_phy_", "pcie_phy_") if hasattr(regs, g + "serdes_dcu_status")), "pcie_phy_serdes_")
v = getattr(regs, prefix + "dcu_status").read()
print(f"ctrl_scratch      0x{regs.ctrl_scratch.read():08x}")
print(f"dcu_status        0x{v:02x}")
print(f"  tx_lol          {v & 1}   (0 = TX PLL locked on the refclk)")
print(f"  rx_lol          {(v >> 1) & 1}")
print(f"  rx_los          {(v >> 2) & 1}")
print(f"  tx_ready        {(v >> 3) & 1}")
print(f"  rx_ready        {(v >> 4) & 1}")
print(f"  init_state      {(v >> 5) & 7} {STATES[(v >> 5) & 7]}")
print(f"tx_idle_delay     {getattr(regs, prefix + 'tx_idle_delay').read()}")
d = getattr(regs, prefix + "detect_status").read()
print(f"detect_status     0x{d:07x}")
print(f"  requests        {d & 0xff}")
print(f"  done            {(d >> 8) & 0xff}   (DCU handshake completed)")
print(f"  timeouts        {(d >> 16) & 0xff}   (DCU never completed: reported as no receiver)")
print(f"  connected       {(d >> 24) & 1}   (last result)")
bus.close()
