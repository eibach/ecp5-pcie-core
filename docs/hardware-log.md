# Hardware log

Dated record of the results on the testbed (see the README), including the dead ends that cost time. Procedures are in `runbook.md`.

## 2026-11-10: the host does not see the slot
Link training could not be tested because the host's BIOS hid the slot's root port (`00:02.1`): `lspci` showed only the root port of the on-board NIC. Neither JTAG-loaded
designs nor a design in the flash changed that while jumper J4 was open or on 1-2 + 3-4. A borrowed PCIe card of another kind in the same slot made the root port appear, which showed that the
slot and the BIOS are fine and that the port is enabled only when a card links during POST.

## 2026-11-11: first link, enumeration, BAR0
Jumper J4 on pins 3 and 5 only, the design in the SPI flash (`ecppack --freq 62.0`, LTSSM autostart), cold boot (after replacing the host's dead RTC battery):
- `lspci -tv`: `00:02.1-[01]----00.0` (at that time with the default IDs of LitePCIe's soft PHY), Region 0 4K, PCIe Endpoint at 2.5GT/s x1, root port DLActive.
- Card side (status page): L0, DL_Active, flow control initialized, DLLPs flowing, receiver detection completing on the real DCU (no timeouts).
- BAR0: the first read of the scratch register (+0x804) returned its reset value 0x12345678, a memory read through the root port, the soft PHY, the register bridge and a completion back. Write and
  readback of 0xa5a5a5a5, 0xdeadbeef, 0x01234567 and 0x80000001 with single 32-bit stores: all OK. A first attempt with `dd` on `/dev/mem` failed because it wrote bytes and the register bridge ignores
  byte enables (only the last byte survives): a test artifact, not a design bug.
- **Reconfiguring the FPGA (JTAG load) under a live link froze the host.** The procedure since then: halt the host, write the flash, cold boot.

## 2026-11-11: MSI
With the `msi_probe` tool (no driver): **memory-write mode** (MSI address a pinned RAM page above 4 GiB, so a 64-bit MSI write): word 0 became 0x00005a5a, the rest of the page untouched, and the
DLL's transmit TLP counter moved by one. **Real interrupt** (local APIC address, vector 0x6e): the kernel logged `No irq handler for vector` for that vector, the interrupt reached the CPU.

## 2026-11-11: IDs, DMA, the Linux driver
The design uses vendor `0x1204` (Lattice, as LitePCIe's driver keeps one vendor ID per FPGA vendor) and device `0xec11`, with a 128 KB BAR0 for the driver's register map and one DMA channel with the loopback.
The unmodified driver, cross-built on the laptop against the headers of the host's exact kernel, bound to the card, read the SoC identifier and ran `dma_test` at 1.66 Gbit/s with 0 errors.
The module build needed `CONFIG_DEBUG_INFO_BTF_MODULES` defined in `autoconf.h` (it changes `struct module`): without it `insmod` failed with "Invalid relocation target".

## 2026-11-11: analog attribute sets
Two sets of DCU analog attributes were compared on the link, the one from the output of Lattice's PCI Express wizard (documented in Yumewatari) and one other set used during bring-up. Both
train, enumerate and run the DMA loopback. The code contributed upstream keeps the wizard set only, as `pcie_analog="wizard"`.

## 2026-11-11: soak and the host-side lost buffers
The first 15 minute `dma_test` soak reported 4 intervals with errors (63488, 2048, 131072 and 1703936 words, exactly 31, 1, 64 and 832 buffers of 2048 words). The link layer was clean (0 replays, 0 receiver
errors, no AER), and the driver logged "Writing too late, N buffers lost": the host program had not serviced the DMA ring because other commands were running on the slow host (SSH logins, a status page).
A soak with no access to the host at all showed **0 errors**. Rule since then: during a soak the host is left alone. Simulations with the core clock slower and faster than the link clock showed no data loss,
which excluded the clock crossing.

## 2026-11-11 night: validation on the bitstream of the final contribution
Upstream had moved (a refactor changed the register map), so the tests were repeated on a bitstream built from the contributed branches (`bench/soft/versa.py`, `--analog wizard`, seed 1, `--ltssm-autostart`,
`--flash-freq 62.0`, OSS CAD Suite 2026-10-30), written to the SPI flash with the host halted, then a cold boot:
- Enumeration `1204:ec11`, Region 0 128K, x1 at 2.5GT/s, DLActive; `scratch_test` OK; `dma_test` 8 s: 1.66 Gbit/s, 0 errors; 6893 interrupts counted by the driver.
- BAR0 stress without DMA: read latency 1.66 us, write plus readback 8.45 us; 60 s: 7.1M write+readback, 28.4M memory words, 0 mismatches; 15 min: 106.5M write+readback and 426.0M memory words, 0 mismatches.
- 15 minute hands-off soak: 4469 samples, mean 1.660 Gbit/s, 22.79M buffers, **0 errors**, no "too late", no AER, link-layer counters unchanged, DCU locked, 800994 interrupts.
- 20 link cycles: **20 of 20**, link back in 0.57 s, 1.66 Gbit/s, 0 errors each time.
- nextpnr (seed 1, `sys` 62.5 MHz, `pcie_link` 75 MHz): `pcie_link` 82.8 MHz, lane clock 87.7 MHz (62.5), `sys` 74.7 MHz; 24400 LUT4 (55%), 10793 FF (24%), 56 of 108 DP16KD.

## Lessons
1. J4 on pins 3 and 5, the design in the SPI flash at 62 MHz with autostart, cold boot: without all three the host never sees the card.
2. Never reconfigure the FPGA under a running link. Halt, flash, cold boot.
3. Leave the host alone during a soak.
4. After rebasing onto a moved upstream, rebuild from the branch tip and repeat the hardware tests: register addresses change.
5. The OSS CAD Suite build of 2026-10-30 packs the design; later builds fail (see `toolchain-notes.md`).
