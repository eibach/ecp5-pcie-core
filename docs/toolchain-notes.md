# Toolchain notes

Installed in user space on 2026-11-09 (no root):

| Component | Version | Where |
|---|---|---|
| OSS CAD Suite | 2026-11-09 | `toolchain/oss-cad-suite` (yosys 0.70+360, nextpnr-ecp5 0.11.1-89, openFPGALoader 1.2.0, iverilog, verilator, gtkwave, prjtrellis) |
| Amaranth | 0.5.10 | `.venv` (+ amaranth-boards 0.0.21, pyserial, pytest) |

`source env.sh` activates both. `scripts/install-toolchain.sh` recreates them.

## Local sysroot for LiteX Verilator sims (no root)
LiteX's sim harness compiles helper modules needing libevent and json-c headers, which this machine lacks.
`scripts/install-toolchain.sh` downloads the Ubuntu `.deb`s (`apt-get download`) and unpacks them with `dpkg -x`
into `toolchain/sysroot/`; `env.sh` adds it to `CPATH`, `LIBRARY_PATH` and `LD_LIBRARY_PATH`. Nothing is installed system-wide.

## Baseline of upstream soft PHY (2026-11-10, litepcie e0ff3dd, liteiclink 12ffe0e)
Run from `dev/litepcie` after `source ../../env.sh`:
- `python -m pytest -q -m "not slow" test/test_soft_*.py` -> **124 passed**, 24 deselected (slow), 4 min 15 s.
- `python bench/soft/sim.py --timer-scale 100 --with-dll` -> SIM OK: L0 + DL_Active, 64/64 packets both ways, Recovery OK (21 s).
- `python bench/soft/sim_endpoint.py` -> SIM OK: config space, BAR0 read (cafe1234), MSI, UR handling (23 s).
Slow-marked tests (`-m slow`) not run yet.

## One step that needs root (not done)
Versa JTAG is an FT2232H (0403:6010). Install the udev rule shipped with the suite, then replug:

    sudo cp toolchain/oss-cad-suite/share/openocd/contrib/60-openocd.rules /etc/udev/rules.d/
    sudo udevadm control --reload && sudo udevadm trigger


## Known issue: nextpnr-ecp5 packing fails on async-set flip-flop chains (toolchain regression)
With OSS CAD Suite **2026-11-09** (nextpnr 0.11.1-89-g445e2a65, yosys 0.70+360) designs containing reset/CDC
synchronizer chains fail in packing:

    ERROR: net '$PACKER_GND_NET' user port 'FD1S3BX_9.M' connected to incorrect net 'FD1S3BX_8_Q'
    ERROR: INTERNAL CHECK FAILED ... Packing design failed.

Seen in (a) Amaranth's stock default `sync` domain (ResetSynchronizer + GSR) and (b) the LiteX soft-PHY stub build
(`bench/versa_timing_stub.py`). **OSS CAD Suite 2026-10-30** (nextpnr 0.11.1-83-g9691ce8c, yosys 0.70+286) builds the
same Amaranth smoke design fine, so it is a regression between the two builds. Not bisected further; suspect (unverified)
nextpnr `a2c647997` "ecp5: Allow constants to be sourced from any tile" (2026-10-31). No matching upstream issue found
(searched YosysHQ/nextpnr 2026-11-10). Not reported yet.

Workaround in use: `toolchain/alt/20261030/oss-cad-suite` (extracted next to the main suite). Use it by prepending
`$PWD/toolchain/alt/20261030/oss-cad-suite/bin` to `PATH` after `source env.sh`.

## Verified
- Amaranth -> yosys -> nextpnr-ecp5 (`--um-45k`, BG381) -> ecppack bitstream for the Versa: OK (reset-less domain).
- `DCUA` + `EXTREFB` primitives are recognised, packed and placed on LFE5UM-45F. A full-config route has NOT been
  tested yet (a bare stub fails routing a constant net; meaningless without a real configuration).
  The first hardware-relevant check is a real DCUA config (SERDES loopback) -- see plan.

## Not verified
- No Versa attached, no JTAG test, no board programming done.
- This laptop (Lenovo 20EQS20D00) is the dev machine, NOT the Fujitsu host.
