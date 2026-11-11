#!/usr/bin/env python3
"""Toolchain smoke test: Amaranth -> yosys -> nextpnr-ecp5 -> ecppack for the Versa (LFE5UM-45F).

  python scripts/smoke_build.py --no-rst   # blinky on a reset-less sync domain (the stock domain fails, see docs/toolchain-notes.md)
"""
import argparse
from amaranth import *
from amaranth.lib import io
from amaranth_boards.versa_ecp5 import VersaECP5Platform


class Top(Elaboratable):
    def elaborate(self, platform):
        m = Module()
        cnt = Signal(26)
        m.d.sync += cnt.eq(cnt + 1)
        for i in range(8):
            m.d.comb += platform.request("led", i).o.eq(cnt[18 + (i % 8)])
        return m


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-rst", action="store_true")
    args = ap.parse_args()

    class Plat(VersaECP5Platform):
        if args.no_rst:
            # The stock default domain (ResetSynchronizer + GSR) currently fails nextpnr-ecp5 packing
            # with oss-cad-suite 2026-11-09 (nextpnr 0.11.1-89), see docs/toolchain-notes.md.
            # Use a plain reset-less domain straight from the board clock instead.
            def create_missing_domain(self, name):
                if name != "sync":
                    return super().create_missing_domain(name)
                m = Module()
                m.domains.sync = ClockDomain(reset_less=True)
                clk = self.request(self.default_clk, dir="-")
                m.submodules.clk_buf = buf = io.Buffer("i", clk)
                m.d.comb += ClockSignal("sync").eq(buf.i)
                return m

    plat = Plat(toolchain="Trellis")
    plat.build(Top(), name="smoke", build_dir="build/smoke", do_program=False)
    print("SMOKE BUILD OK")
