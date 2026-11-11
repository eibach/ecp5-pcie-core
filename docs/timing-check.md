# Soft PHY timing/resource check on ECP5 (LFE5UM-45F, Versa) -- 2026-11-10

Design: `bench/versa_timing_stub.py` = LitePCIe `SoftPCIEPHY` (Gen1 x1, 64-bit LitePCIe datapath) + MMAP/BAR0 (4 KB, no DMA)
+ UARTBone, with a fabric-only stub lane (soft-PHY simulation model, TX looped to RX in a second 62.5 MHz domain)
instead of a DCU. Clocks from one ECP5 PLL. The link never trains: this only measures timing/area of the logic above the lane.
Toolchain: OSS CAD Suite 2026-10-30 (nextpnr 0.11.1-83-g9691ce8c, yosys 0.70+286; the 2026-11-09 build fails packing, see
toolchain-notes.md). Device string from litex-boards: LFE5UM-45F-**8**BG381C (speed grade 8 confirmed: the board's chip is marked LFE5UM-45F 8BG381I; litex-boards says 8BG381C, the I is the industrial temperature grade and nextpnr does not distinguish it).
Single placement seed (1) per row, final post-route Fmax from nextpnr.

| Run | pcie_link | sys | pcie_link Fmax | sys Fmax | lane clk 62.5 MHz Fmax (pipe / pipe_rx) | Result |
|---|---|---|---|---|---|---|
| 1 | 125 MHz | 125 MHz | 89.1 | 103.9 | 84.4 / 108.5 | FAIL |
| 2 | 100 MHz | 100 MHz | 90.5 | 93.9 | 84.2 / 114.3 | FAIL |
| 3 | 75 MHz | 75 MHz | 92.8 | 94.0 | 89.3 / 113.7 | **PASS** |

Utilization (run 1, BAR0 only, no DMA, no real DCU): TRELLIS_COMB 9527/43848 (21%; 7785 logic LUT4, 896 carry, 360 RAM),
TRELLIS_FF 4500 (10%), DP16KD 7/108 (6%), EHXPLLL 1/4.

## Critical paths
- pcie_link (run 1): retry-buffer RAM (DP16KD) output -> ~30 LUT4 levels -> `datalinklayer_tx_lcrc_state` (TX LCRC, CRC-32 over a
  64-bit word). 11.2 ns total (5.9 logic + 5.3 routing) vs 8 ns. Pipelining this path in `dll/` is the way to 125 MHz.
- sys (run 1): LitePCIe TLP depacketizer dispatcher, routing-dominated (6.9 ns routing vs 2.7 ns logic).

## Functional check of a slower link clock (Verilator, 2026-11-10)
`dev/litepcie/bench/soft/sim.py` and `sim_endpoint.py` hard-wired lane = link/2. Patched (working tree of our
`ecp5-soft-phy` branch, not committed) with `--link-freq F`: for F != 125 MHz the 62.5 MHz lane clock is an independent
Verilator clocker (the simulated periods keep the exact lane/link ratio at round values; layer timers get F).
Gen2 is not supported in that mode.

| Test | link 125 MHz (unchanged) | link 100 MHz | link 75 MHz (1.2 x lane) |
|---|---|---|---|
| `sim.py --with-dll`: L0, DL_Active, 64+64 packets, Recovery | OK (identical cycle counts to baseline) | OK | OK |
| same with `--corrupt-every 5 --drop-dllp-every 7` (replays) | OK (52 replays) | not run | OK (26 replays, 24 rx errors recovered) |
| `sim_endpoint.py`: config space, BAR0 read cafe1234, MSI, UR | OK | OK | OK |

Conclusion: **running pcie_link/sys at 75 MHz is functionally safe in simulation and closes timing on ECP5 (92.8 MHz Fmax),
so no DLL change is needed for the milestone.** 125 MHz would require pipelining the DLL TX LCRC path (upstream work).
Limits: the simulated clocks are exactly frequency-locked (no ppm offset between lane and link); not tested with DMA
(`sim_soc.py`), Gen2, or on hardware.

Caveat: the stub took `pcie_link` from the Versa's local 100 MHz oscillator. On hardware it must come from the host refclk
(like `pcie_pipe`), see `serdes-adapter-design.md`. The timing result is unaffected; the locked-clock simulation is the right model
once both clocks derive from the host refclk.

## Still unknown
- Real DCU clocking/placement effects, other seeds, 
- DMA variant (`--ndmas 1`) not built.
