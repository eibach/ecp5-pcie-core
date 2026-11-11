# Bring-up runbook (the procedure that works)

How to make a real host enumerate the ECP5 soft PHY on a Lattice ECP5 Versa, and how to diagnose each failure. Every statement here was observed
on the testbed of the README. The dated record of the results is `hardware-log.md`.

## 1. The setup that works
| Item | Setting |
|---|---|
| Host | Fujitsu FUTRO S920 (mainboard D3313-G1, BIOS V4.6.5.4 R1.16.0), Arch Linux 6.12.10, reached over SSH from the laptop. Set a root password or a key: the image we used allowed an empty password |
| Slot | The Versa stands in the blue x16 slot of the mainboard (electrically x4, one lane used), directly, no riser |
| Power | **Only from the slot.** No external supply was ever connected. The slot supply stays on in a warm reboot, so the card keeps its FPGA configuration across `reboot` |
| **J4** | **One jumper on pins 3 and 5, nothing else.** Pins 1+3 are the net PRSNT of the clock manager, pin 5 is GND, pin 2 is PRSNT1#, pin 4 is PRSNT3#. 3-5 grounds PRSNT so that the clock manager passes the slot's 100 MHz reference clock. Not the 1-2 + 3-4 of the board guide's figure 7, and not open: with those the BIOS never enabled the slot's root port |
| Bitstream | In the **SPI flash**, built with `--flash-freq 62.0` and LTSSM autostart (section 3), so the FPGA is configured and linking before the BIOS scans PCIe |
| USB | The board's USB cable to the laptop: JTAG on the first FT2232H interface, UARTBone on the second (`/dev/serial/by-id/*if01-port0`) |
| Host RTC | A dead CR2032 stopped every cold boot at a "date/time invalid" prompt: replace it |

Why the order matters: the BIOS decides at POST whether to enable the slot's root port (`00:02.1`) and hides it when no card links in time. With the design absent at that moment
the port simply does not exist, and nothing can be done from Linux afterwards. A borrowed PCIe card of another kind showed that the slot and the BIOS are fine.

## 2. Tools
- Toolchain: `setup/install-toolchain.sh` then `source env.sh` (OSS CAD Suite build of 2026-10-30; later builds fail to pack the design, see `toolchain-notes.md`).
- The gateware and the Linux driver: `setup/setup.sh` fetches the pinned LitePCIe and LiteICLink, `setup/build.sh` builds the Versa image (section 3).
- Programmer: `openFPGALoader -b ecp5_evn` over JTAG (a udev rule for the FT2232H is needed once).
- `scripts/`: `versa_monitor.py` (status page over UARTBone), `pcimem32.c`, `msi_probe.c`, `bar0_stress.c` (static host tools), `link_cycles.sh`, `soak_summary.sh`, `host_pcie_report.sh`.

## 3. Build
```sh
setup/setup.sh && source env.sh
setup/build.sh                 # = LitePCIe bench/soft/versa.py --build --with-analyzer --ltssm-autostart --flash-freq 62.0 --driver
# bitstream: build/versa/gateware/lattice_versa_ecp5.bit, registers: build/versa/csr.csv, Linux driver sources: build/versa/driver/
```
The options of the bench are in LitePCIe's manual (`--analog wizard|default`, `--sys-clk-freq`, `--link-freq`, `--flash-freq`, `--seed`). `--flash-freq 62.0` makes `ecppack --freq 62.0`: the
FPGA reads its flash at 62 MHz (the default is 2.4 MHz, about 1.3 s for the file). Without `--ltssm-autostart` the LTSSM waits for the enable register.

## 4. Writing the flash safely
**Never reconfigure the FPGA (JTAG load, flash tool, PROGRAMN) while Linux has the card enumerated: the host froze when we did.** Halt the host first (powered, CPU stopped); the board stays powered from the slot:
1. `ssh root@<host> systemctl halt` (not `poweroff`). The host stops answering; the fans keep running.
2. `openFPGALoader -b ecp5_evn --detect` (idcode `0x01112043`: LFE5UM-45F).
3. **Back up the original flash content first**, twice, and compare (the card ships with a design in its flash; keep the file, it is the vendor's):
   `openFPGALoader -b ecp5_evn --dump-flash --file-size 16777216 -o 0 flash_A.bin` (and `flash_B.bin`), `cmp` them.
4. Write and verify: `openFPGALoader -b ecp5_evn -f --verify build/versa/gateware/lattice_versa_ecp5.bit` (3 s; the tool then reloads the FPGA from flash). The monitor already shows the link state at this point, because the host's root port trains in hardware.
5. Restore the original design if needed: `openFPGALoader -b ecp5_evn -f --file-type raw --verify flash_A.bin`.

Volatile loads (no flash): `openFPGALoader -b ecp5_evn <file>.bit`, only with the host halted or off.

## 5. Boot and verify
1. Hold the host's power button until it switches off (a halted host ignores a short press), then power it on: a cold boot.
2. `lspci -tv`: expect `00:02.1-[01]----00.0 Lattice Semiconductor Corporation Device ec11`; `lspci -vv -s 01:00.0`: Region 0 128K, MSI, PCIe Endpoint at 2.5GT/s x1.
3. Driver: build `litepcie.ko` and the tools from `build/versa/driver/` for the host kernel (a host without compiler or headers: cross-build elsewhere against the headers package of the exact kernel, see the note below), then
   `insmod litepcie.ko; ./litepcie_util info; ./litepcie_util scratch_test; ./litepcie_util dma_test -w 32 -t 8` (expect 1.66 Gbit/s and 0 errors).
4. Without the driver: `gcc -O2 -static -o pcimem32 scripts/pcimem32.c`, then on the host `R=/sys/bus/pci/devices/0000:01:00.0/resource0; ./pcimem32 $R 0x804` reads `ctrl_scratch` (0x12345678). Writes must be single 32-bit stores: `dd` on `/dev/mem` writes bytes, and the register bridge ignores byte enables.
5. Card side: `python3 scripts/versa_monitor.py --csr-csv build/versa/csr.csv --uart-port auto`, then http://localhost:8765 (status page, event log, buttons, DMA and MSI panels, these notes under "Docs").

Note on building the module for a host without a toolchain: extract the Arch `linux-headers` package of the exact kernel version, keep `CONFIG_DEBUG_INFO_BTF_MODULES` defined in `include/generated/autoconf.h` (it changes `struct module`; if it
is off the module fails with "Invalid relocation target") while keeping it off in `include/config/auto.conf` (skips the BTF step), then `make KERNEL_PATH=<headers>/build litepcie.ko` and `strip --strip-debug`.

## 6. The tests behind the results
- **BAR0 stress without DMA:** `scripts/bar0_stress.c` (driver unloaded): timed reads, write with readback of pseudo-random words, repeated reads of the identifier memory compared with the first read. 15 minutes: 106.5M write and readback, 426.0M memory words, 0 mismatches.
- **DMA soak:** `litepcie_util dma_test -w 32 -t 900`. **Leave the host alone while it runs**: start it, wait, read the result once (`scripts/soak_summary.sh`). On this slow host, SSH logins or other work during the test make the program miss the DMA ring: the driver logs "Writing too late, N buffers lost" and the data check counts whole buffers (2048 words each) as errors.
- **Link cycles:** `scripts/link_cycles.sh 20`: driver unload, card removal, Retrain Link on the root port, rescan, driver load, `scratch_test` and a 3 s DMA test, with the kernel log checked each time.
- **MSI:** the driver counts the interrupts of the DMA tests (`/proc/interrupts`); without the driver `scripts/msi_probe.c` sends an MSI into a pinned RAM page or the local APIC.

## 7. Failure map
| Symptom | Meaning / what to try |
|---|---|
| `lspci` shows only `00:02.2` (the NIC) | The slot's root port is hidden: the BIOS saw no card linking at POST. Check J4 (3-5 only), that the card is configured at power-up (bitstream in flash, 62 MHz, autostart), a cold boot, the RTC battery. A different PCIe card in the slot is the control experiment |
| Port present, no card on bus 01 | Link not trained: monitor LED panel and LTSSM state (Detect: no receiver seen; Polling: no TS from the host; Configuration or Recovery loops: transmit or receive quality) |
| Link up in the monitor, `lspci` empty, host halted | Expected: a halted host does not enumerate. Boot Linux |
| Enumerated, `scratch_test` wrong | Check the register offsets in `csr.csv` (the analyzer variant puts the analyzer bank first: `ctrl` at 0x800) |
| Writes show only the top byte | The test wrote bytes (`dd` on `/dev/mem`): use a single 32-bit store |
| Host froze | The FPGA was reconfigured under a live link: power-cycle; always halt first |
| `insmod`: Invalid module format / Invalid relocation target | Headers whose `struct module` differs from the kernel's (see the note in section 5) |
| `dma_test` reports errors in whole buffers | "Writing too late" in `dmesg`: the host program is too slow (see section 6); not a PHY error if the link-layer counters stay at zero |
| Monitor "no link to the board" | The FT2232H re-enumerated after a power cycle: `--uart-port auto` finds the device again |

## 8. Diagnostics worth knowing
- With the LTSSM disabled and the host in Polling.Compliance, a root port may send the compliance pattern (K28.5, D21.5, K28.5, D10.2): K28.5 at every other symbol in a LiteScope capture proves the receive chain without any transmit.
- The delay of the transmit electrical idle against the EIOS is the register `tx_idle_delay` (lane clock cycles, default 2).
- The DCU status register (`dcu_status`) and the detection counters (`detect_status`): `scripts/dcu_status.py`.
- The DCU PLL "locked" flag does not check the absolute frequency (a 156.25 MHz configuration also reads "locked" on a 100 MHz reference clock): only a decoded training sequence proves the clock.
- No transmit de-emphasis is configured; it is a candidate if a host counts errors on our transmitter.

## 9. What was not done
Gen2 (the non-5G part tops out at 3.2 Gbit/s), x2 or wider, ASPM and L0s, spread-spectrum clocking and clock-offset tests, a second host.
