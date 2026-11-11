/* Copyright (c) 2026 Dirk Eibach
 * SPDX-License-Identifier: BSD-2-Clause
 */
/* Minimal 32-bit register access through a PCI resource file, for hosts without python/devmem:
 *   pcimem32 /sys/bus/pci/devices/0000:01:00.0/resource0 <offset> [value]
 * One mmap, one volatile 32-bit load or store per access (a single 4-byte PCIe memory TLP). */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s <resourceN> <offset> [value]\n", argv[0]); return 2; }
    int fd = open(argv[1], O_RDWR | O_SYNC);
    if (fd < 0) { perror("open"); return 1; }
    unsigned long off = strtoul(argv[2], 0, 0);
    long pg = sysconf(_SC_PAGESIZE);
    unsigned long base = off & ~(pg - 1);
    volatile uint32_t *map = mmap(0, pg, PROT_READ | PROT_WRITE, MAP_SHARED, fd, base);
    if (map == MAP_FAILED) { perror("mmap"); return 1; }
    volatile uint32_t *reg = map + ((off - base) / 4);
    if (argc > 3) *reg = (uint32_t)strtoul(argv[3], 0, 0);
    printf("0x%08x\n", *reg);
    return 0;
}
