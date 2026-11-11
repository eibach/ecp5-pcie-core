#!/usr/bin/env python3
# Copyright (c) 2026 Dirk Eibach
# SPDX-License-Identifier: BSD-2-Clause
"""BAR0 32-bit read/write test of the bring-up design, run ON THE HOST as root once the card enumerated.

BAR0 offsets are added to the SoC's CSR origin, so the SoC's first CSRs are the "4 KB of registers": the test uses
ctrl_scratch (reset value 0x12345678) from the build's csr.csv. Writes patterns and restores the original value.

  sudo python3 scripts/bar0_test.py --bdf 0000:03:00.0 --csr-csv build/bringup_l75_s1/csr.csv
"""
import argparse, mmap, os, struct, sys

def csr_offset(csv_path, name):
    for line in open(csv_path):
        f = line.strip().split(",")
        if f[0] == "csr_register" and f[1] == name:
            return int(f[2], 0)
    sys.exit(f"{name} not found in {csv_path}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bdf", required=True, help="PCI address, e.g. 0000:03:00.0")
    ap.add_argument("--csr-csv", required=True)
    ap.add_argument("--register", default="ctrl_scratch")
    args = ap.parse_args()

    off = csr_offset(args.csr_csv, args.register)
    path = f"/sys/bus/pci/devices/{args.bdf}/resource0"
    size = os.stat(path).st_size
    print(f"{path}: {size} bytes, {args.register} at BAR0+0x{off:x}")
    if off + 4 > size:
        sys.exit("register outside BAR0")
    fd = os.open(path, os.O_RDWR | os.O_SYNC)
    bar = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
    rd = lambda: struct.unpack_from("<I", bar, off)[0]
    def wr(v): struct.pack_into("<I", bar, off, v)

    orig = rd()
    print(f"read  0x{orig:08x}" + ("  (reset value)" if orig == 0x12345678 else ""))
    errors = 0
    for pattern in [0xa5a5a5a5, 0x5a5a5a5a, 0xffffffff, 0x00000000, 0xdeadbeef, 0x01234567]:
        wr(pattern)
        got = rd()
        ok = got == pattern
        errors += not ok
        print(f"write 0x{pattern:08x} -> read 0x{got:08x} {'OK' if ok else 'MISMATCH'}")
    wr(orig)
    print("restored" if rd() == orig else "RESTORE FAILED")
    print("BAR0 TEST", "OK" if errors == 0 else f"FAILED ({errors} mismatches)")
    sys.exit(1 if errors else 0)

main()
