# Prior work and credits

This project would not exist without the work below. The licenses are those found in the repositories.

| Work | License | What this project took | Credit |
|---|---|---|---|
| [LitePCIe](https://github.com/enjoy-digital/litepcie), [LiteICLink](https://github.com/enjoy-digital/liteiclink), [LiteX](https://github.com/enjoy-digital/litex) (Florent Kermarrec, Enjoy-Digital) | BSD-2-Clause | The soft PHY, the DLL/TL, the DMA, the Linux driver, and the `SerDesECP5` wrapper this builds on | The core is theirs; the ECP5 adapter only adds the lane adapter, a DCU model and options |
| [Yumewatari](https://github.com/whitequark/Yumewatari) (whitequark, 2018) | 0BSD | DCU PCI Express mode attributes, the analog values of Lattice's PCI Express wizard (`pcie_analog="wizard"`), the receiver detection sequence | Values and sequence; no source file copied |
| [katsuo-pcie](https://github.com/zyp/katsuo-pcie) and [litepcie-katsuo-poc](https://github.com/zyp/litepcie-katsuo-poc) (Vegard Storheil Eriksen, 2026) | MIT | Confirmation of the PCIe-mode attributes (its SERDES is derived from Yumewatari); showed LitePCIe running on the Versa | No code taken |
| [ECP5-PCIe](https://github.com/ECP5-PCIe/ECP5-PCIe) | none | Read for background | No code or values taken (it has no license) |
| Lattice documents: TN1261 "ECP5 and ECP5-5G SERDES/PCS Usage Guide", the ECP5 Versa board guide | Lattice documents | Detection handshake timing, board facts (J4, clocking) | Cited by title; not redistributed |
| Project Trellis, yosys, nextpnr, openFPGALoader, OSS CAD Suite | various open licenses | The toolchain | |

The upstream discussion that started this work is [litepcie issue #20](https://github.com/enjoy-digital/litepcie/issues/20).
