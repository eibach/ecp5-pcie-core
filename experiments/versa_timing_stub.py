#!/usr/bin/env python3
# Copyright (c) 2026 Dirk Eibach
# SPDX-License-Identifier: BSD-2-Clause

#
# Timing / resource feasibility check of LitePCIe's soft PHY on the ECP5 Versa (LFE5UM-45F).
#
# NOT a hardware design: the transceiver is replaced by a fabric-only lane (the simulation model of
# the soft PHY, tx looped back to rx through a register in a separate clock domain). A real DCU
# delivers the same raw 40-bit words per lane clock, so the logic above the lane, which is what we
# are measuring, is the same as the final design. The link never trains, the design is only built.
#
#   python bench/versa_timing_stub.py --build [--speed 8] [--ndmas 0] [--sys-clk 125e6] [--seed N]
#

import argparse

from migen import *

from litex.gen import *

from litex_boards.platforms import lattice_versa_ecp5

from litex.soc.cores.clock import ECP5PLL
from litex.soc.integration.soc_core import SoCMini
from litex.soc.integration.builder import Builder

from litepcie.phy.soft           import SoftPCIEPHY
from litepcie.phy.soft.serdes.model import PCIeSerDesModel

# Stub lane -----------------------------------------------------------------------------------------

class StubSerDes(PCIeSerDesModel):
    """Simulation model lane with its own TX looped back to its RX (recovered clock domain)."""
    def __init__(self):
        PCIeSerDesModel.__init__(self, nlanes=1, tx_cd="pcie_pipe", rx_cd="pcie_pipe_rx")
        # Loopback TX -> register in the RX clock domain (as a CDR would re-time the data).
        rx_data = Signal(40)
        self.sync.pcie_pipe_rx += rx_data.eq(self.tx[0].data)
        self.comb += [
            self.rx[0].valid.eq(1),
            self.rx[0].data.eq(rx_data),
            self.rx_idle[0].eq(self.tx_idle[0]),
            self.partner_rate.eq(self.applied_rate),
        ]

    def add_timing_constraints(self, sys_clk):
        pass # Clocks come from the PLL: nextpnr derives their constraints.

# CRG -----------------------------------------------------------------------------------------------

class CRG(LiteXModule):
    def __init__(self, platform, sys_clk_freq, link_freq=125e6):
        self.cd_sys           = ClockDomain()
        self.cd_pcie_link     = ClockDomain()
        self.cd_pcie_pipe     = ClockDomain()
        self.cd_pcie_pipe_rx  = ClockDomain()

        self.pll = pll = ECP5PLL()
        pll.register_clkin(platform.request("clk100"), 100e6)
        pll.create_clkout(self.cd_sys,          sys_clk_freq)
        pll.create_clkout(self.cd_pcie_link,    link_freq)
        pll.create_clkout(self.cd_pcie_pipe,    62.5e6)
        pll.create_clkout(self.cd_pcie_pipe_rx, 62.5e6)

# SoC -----------------------------------------------------------------------------------------------

class StubSoC(SoCMini):
    def __init__(self, platform, sys_clk_freq=125e6, link_freq=125e6, ndmas=0, bar0_size=0x1000):
        SoCMini.__init__(self, platform, sys_clk_freq, ident="Soft PHY timing stub (no SERDES)", ident_version=True)
        self.crg = CRG(platform, sys_clk_freq, link_freq)
        self.add_uartbone()

        self.serdes   = serdes = StubSerDes()
        self.pcie_phy = pcie_phy = SoftPCIEPHY(platform, None, serdes=serdes,
            data_width = 64,
            bar0_size  = bar0_size,
            link_freq  = link_freq,
        )
        self.add_pcie(phy=pcie_phy, ndmas=ndmas, address_width=32, with_dma_loopback=bool(ndmas))

        leds = platform.request_all("user_led")
        phy  = pcie_phy.phy_layer
        self.comb += [
            leds[0].eq(~serdes.ready),
            leds[1].eq(~phy.ltssm.link_up),
            leds[2].eq(~phy.ltssm.l0),
        ]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build",   action="store_true")
    ap.add_argument("--speed",   default="8", choices=["6", "7", "8"], help="ECP5 speed grade (the platform says 8).")
    ap.add_argument("--ndmas",   default=0, type=int)
    ap.add_argument("--sys-clk", default=125e6, type=float)
    ap.add_argument("--link-freq", default=125e6, type=float, help="pcie_link clock (LTSSM, DLL, TL).")
    ap.add_argument("--seed",    default=1, type=int)
    ap.add_argument("--output-dir", default=None)
    args = ap.parse_args()

    platform = lattice_versa_ecp5.Platform(device="LFE5UM", toolchain="trellis")
    platform.device = platform.device.replace("-8BG", f"-{args.speed}BG")
    soc      = StubSoC(platform, sys_clk_freq=args.sys_clk, link_freq=args.link_freq, ndmas=args.ndmas)
    out      = args.output_dir or f"build/stub_dma{args.ndmas}_sp{args.speed}_l{args.link_freq/1e6:g}_sys{args.sys_clk/1e6:g}_s{args.seed}"
    builder  = Builder(soc, output_dir=out, csr_csv="csr.csv")
    builder.build(run=args.build, seed=args.seed, timingstrict=False)

if __name__ == "__main__":
    main()
