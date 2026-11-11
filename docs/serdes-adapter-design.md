# ECP5 PCIeSerDes adapter: gap analysis and plan (2026-11-10)

Target: a `PCIeSerDes` subclass (soft PHY lane contract, `dev/litepcie/litepcie/phy/soft/serdes/base.py`) for one
ECP5 DCU channel, Gen1 only. Built on LiteICLink `SerDesECP5` (`data_width=40, with_oob=True`).

## What SerDesECP5 already gives
- Raw 10BSER mode, fabric 8b/10b, 40-bit words on a CLKDIVF/2 clock = linerate/40 = **62.5 MHz at 2.5 Gbps**, which is
  the `pcie_pipe` lane clock. DCU-side gearbox clock linerate/20 = 125 MHz (easy for the fabric).
- PLL config search accepts a 100 MHz refclk with linerate 2.5e9 (x25, /1 or other).
- SCI access (polarity, loopback, EI via CH-02 bit 6), init FSM with OOB variant, PRBS TX/RX, raw TX data.
- `FFC_EI_EN` input driven from `tx_idle` in the `with_oob` path.

## Gaps for PCIe (all from Yumewatari, 0BSD, `platform/lattice_ecp5.py`, or katsuo-pcie, MIT)
| Item | State in SerDesECP5 | PCIe need / source |
|---|---|---|
| `D_TX_MAX_RATE` | "5.0" | "2.5" for Gen1 (Yumewatari line ~228); also `CHX_CDR_MAX_RATE` |
| `CHX_PCIE_MODE` | absent | "0b1" (Yumewatari, katsuo) |
| Receiver detect | absent | `FFC_PCIE_DET_EN` high >=120 ns while TX idle, then `FFC_PCIE_CT` for 4 byte clocks, wait `FFS_PCIE_DONE` low->high, sample `FFS_PCIE_CON` (TN1261 sequence, Yumewatari det_fsm). Maps to `lane.tx_detect_rx/detect_done/rx_detected`. katsuo has it commented out: treat as unproven. |
| TX electrical idle | `tx_idle` -> `FFC_EI_EN` | map `lane.tx_elecidle`; check output common mode / idle exit timing |
| RX electrical idle | `FFS_RLOS` as `rx_idle` | `lane.rx_elecidle` through `add_rx_elecidle_filter`; `RX_LOS_LVL` threshold is a wizard value, tune on hardware |
| TX/RX polarity | SCI CH-01 bits | `lane.tx_polarity/rx_polarity` (PCIe lane polarity inversion in training) |
| `rx_reset` | init FSM | re-arm CDR/PCS reset |
| Loopback | `loopback` -> SCI CH-15 lb_ctl | map `PCIeSerDes.loopback` |
| Lane datapath | n/a | `add_lane_datapaths` wants Symbols from/to the transceiver with 8b/10b errors already substituted: check how `s7.py` hooks it, do 8b/10b in fabric (`Encoder/Decoder` from SerDesECP5) |
| Elastic buffer / clock compensation | CTC bypassed | RXLaneDatapath does recovered-clock -> pcie_pipe crossing and SKP removal itself |
| Clocking | PLL + CRG outside | new `ECP5PCIeClocking` (see below) |

## Status (2026-11-10, end of day 1)
- Done: `SerDesECP5(pcie=True)` in liteiclink (PCIE_MODE, 2.5 Gbps rate attributes, detect ports, raw `rx_los`);
  `litepcie/phy/soft/serdes/ecp5.py` (`ECP5PCIeSerDes`, `ECP5PCIeDetect`, `ECP5PCIeClocking`);
  `test/test_soft_ecp5.py` (detect FSM against a DCU behaviour model: present, absent, no answer; Verilog elaboration
  checks of the DCUA PCIe-mode parameters). liteiclink suite still 91 passed. Both uncommitted on branch `ecp5-soft-phy`.
- Real DCU build: `bench/versa_pcie_bringup.py --build` (soft PHY + BAR0 + uartbone + this adapter) packs, places, routes and
  produces a bitstream with nextpnr 2026-10-30 / ecppack (LFE5UM-45F-8BG381). Final Fmax: pcie_link 92.5 (75 MHz target),
  sys 86.6 (75), pcie_pipe 85.0 (62.5), recovered rx 100.3 (62.5), DCU clock 200 (125). 10641 LUT4 (24%), 5015 FF, 7 EBR, 1 DCUA.
  Nothing has run on hardware: PASS here only means it builds and meets the static timing.
- Simulation with a DCU behavioural model (`litepcie/phy/soft/serdes/ecp5_model.py`: replaces the DCUA instance and CLKDIVF; serial
  loopback or line between two models, arbitrary bit offset, SCI polarity bits, loss of signal from the partner's idle, TX/RX lock
  delays, TN1261 detect handshake): the REAL `SerDesECP5` (init FSM, 40-bit gearboxes, symbol aligner, SCI reconfiguration) and
  our adapter run under it.
  - `test/test_soft_ecp5_lane.py` (Migen sim, loopback): ready, TS/packets at bit offsets 0/1/4/9, polarity, electrical idle
    (TX `ready` kept while the RX signal is lost), receiver detection present/absent. 5 pass.
  - `bench/soft/sim.py --serdes ecp5 --link-freq 75e6` (Verilator, two adapters, two LTSSMs): L0, 64+64 TLPs, Recovery, again;
    with `--with-dll`: DL_Active; with `--corrupt-every 5 --drop-dllp-every 7`: 101 replays, SIM OK. Also in `test_soft_sim.py` (slow).
  - What this does NOT prove: the real DCU's behaviour. The model encodes our reading of TN1261/Yumewatari and of SerDesECP5's
    own use (lock/LOS timing, SCI latency, EI via FFC_EI_EN, bit order of the 10BSER bus, no ppm offset). Hardware will decide.
- Design decision found by the simulation work: the soft PHY's LTSSM is enabled only while `serdes.ready`. SerDesECP5's OOB init FSM clears
  `rx_ready` whenever RLOS is high (partner idle) and relocks on its own, so `lane.ready`/`serdes.ready` follow `tx_ready` only
  (`serdes.rx_ready` is a separate status for LEDs/debug). Otherwise the LTSSM would restart every time the host goes idle.
  Side effect to watch on hardware: our first TS1s may be lost while the RX relocks after the host starts transmitting.
- nextpnr cannot route the EXTREFB `REFCLKO` into a fabric PLL input ("Failed to find a route for arc ... serdes_clkin_signal").
  So `pcie_link` comes from a PLL fed by the DCU's 125 MHz TX clock (still derived from the host refclk), and `pcie_link` is
  in reset while the DCU TX PLL is not running.
- `sys` comes from the local oscillator (independent), so UART debug works without the slot: the soft PHY already crosses
  sys <-> pcie_link (the earlier "separate debug domain" worry is moot).
- nextpnr ignores LiteX false-path constraints; cross-domain paths are not timed (only synchronizers/FIFOs cross).

## Clocking: correction to the functional check
On Xilinx the soft PHY derives BOTH `pcie_link` and `pcie_pipe` from the **host's** 100 MHz refclk (common clock
architecture: frequency-locked, tracks the host's spread-spectrum clocking). The timing stub used the Versa's
local 100 MHz oscillator for `pcie_link`. That is wrong for hardware: the host refclk and the local oscillator
differ by tens of ppm plus up to 5000 ppm SSC, and the soft PHY expects its fixed clocks to be locked to the lane clock.

Implemented: `pcie_pipe` is the DCU's TX clock /2 (62.5 MHz) and `pcie_link` (75 MHz) comes from an `ECP5PLL` fed by the
DCU's 125 MHz TX clock (the EXTREFB `REFCLKO` route to a fabric PLL fails in nextpnr). Both derive from the host refclk,
75:62.5 = 6:5 exactly, so the "frequency-locked" simulation is the right model. `sys` is from the local oscillator.
Consequence: the PHY side of the fabric only runs with the card in the slot (refclk present).

## Preparation that needs no board (in order)
1. Simulation model of the DCU in PCIe mode (rx detect handshake, idle, polarity, loopback) so the adapter can run in
   Verilator against the existing soft PHY sims (`PCIeSerDesModel` is the fabric-only model; this adds the DCU-facing logic).
2. The adapter `ecp5.py` + `ECP5PCIeClocking`, unit tests for the detect FSM / EI / polarity mapping.
3. First real DCUA build: loopback / PRBS bring-up design for the Versa (routes, bitstream, pin constraints for refclk
   Y11/Y12, rx Y5/Y6, tx W4/W5, perst A6), with UART/LED status.
4. Debug infrastructure: LiteScope-style capture buffer of symbols and LTSSM state with trigger, over UART/uartbone;
   LTSSM state on the 8 LEDs.
5. Host-side checklist script (setpci retrain, rescan, lspci -vvv, dmesg/AER capture) as a script that only prints/collects.
6. Bring-up runbook ordered by risk: (a) JTAG/ID, (b) PLL locked LED, (c) DCU loopback PRBS, (d) detect with host, (e) L0, (f) enumerate.

## Open questions
- Does the 45F `LFE5UM` (non-5G) accept the same DCUA attribute set as 5G? (`D_TX_MAX_RATE`/CDR_MAX_RATE "2.5"
  should be within range; verify the bitstream builds and ecppack accepts it.)
- RX_LOS threshold and PCIe differential levels (host TX 800-1200 mVpp, AC-coupled; the Versa slot has caps?).
- Versa 3.3 V / 12 V slot power: board needs its own supply, never from the slot.
