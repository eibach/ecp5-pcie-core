/* Copyright (c) 2026 Dirk Eibach
 * SPDX-License-Identifier: BSD-2-Clause
 */
/* MSI test helper for a host without a driver for the card (run as root on the host).
 *
 *   msi_probe mem  <bdf> <seconds> [data]    MSI address = a pinned host RAM page (below 4 GiB, physical address from
 *                                            /proc/self/pagemap): watch the endpoint's MSI memory write arrive, no CPU
 *                                            interrupt involved.
 *   msi_probe apic <bdf> <vector> <seconds>  MSI address = local APIC 0xFEE00000, data = <vector> (edge, fixed): a real
 *                                            interrupt to CPU 0. Use a vector with no handler (ex: 0x77): the kernel logs
 *                                            "No irq handler for vector" in dmesg, which proves the delivery.
 *
 * Both modes program the MSI capability (Message Address/Data, Enable) in config space through sysfs, wait while the
 * card is told (over UART, from elsewhere) to send an MSI, and restore the capability on exit (MSI disabled again).
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include <sys/mman.h>

static int cfg;
static uint32_t rd32(int o) { uint32_t v = 0; if (pread(cfg, &v, 4, o) != 4) perror("pread"); return v; }
static uint16_t rd16(int o) { uint16_t v = 0; if (pread(cfg, &v, 2, o) != 2) perror("pread"); return v; }
static void wr32(int o, uint32_t v) { if (pwrite(cfg, &v, 4, o) != 4) perror("pwrite"); }
static void wr16(int o, uint16_t v) { if (pwrite(cfg, &v, 2, o) != 2) perror("pwrite"); }
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }

static uint64_t phys_of(void *p) {
    int pm = open("/proc/self/pagemap", O_RDONLY);
    uint64_t e = 0;
    if (pm < 0 || pread(pm, &e, 8, ((uintptr_t)p / 4096) * 8) != 8) { perror("pagemap"); return 0; }
    close(pm);
    if (!(e >> 63)) return 0;
    return (e & ((1ULL << 55) - 1)) * 4096;
}

int main(int argc, char **argv) {
    if (argc < 4) { fprintf(stderr, "usage: %s mem <bdf> <seconds> [data] | apic <bdf> <vector> <seconds>\n", argv[0]); return 2; }
    int apic = !strcmp(argv[1], "apic");
    char path[256];
    snprintf(path, sizeof path, "/sys/bus/pci/devices/%s/config", argv[2]);
    cfg = open(path, O_RDWR);
    if (cfg < 0) { perror(path); return 1; }

    /* Find the MSI capability (id 0x05). */
    int cap = rd16(0x34) & 0xff, found = 0;
    for (int n = 0; n < 48 && cap >= 0x40; n++) {
        if ((rd16(cap) & 0xff) == 0x05) { found = 1; break; }
        cap = (rd16(cap) >> 8) & 0xff;
    }
    if (!found) { fprintf(stderr, "no MSI capability\n"); return 1; }
    uint16_t ctrl = rd16(cap + 2);
    int is64 = !!(ctrl & 0x80);
    int off_lo = cap + 4, off_hi = cap + 8, off_dat = is64 ? cap + 12 : cap + 8;
    uint32_t s_lo = rd32(off_lo), s_hi = is64 ? rd32(off_hi) : 0; uint16_t s_dat = rd16(off_dat);
    printf("MSI capability at 0x%02x: control 0x%04x (%s, enabled=%d, MME=%d, capable=%d vectors)\n", cap, ctrl,
        is64 ? "64-bit" : "32-bit", ctrl & 1, (ctrl >> 4) & 7, 1 << ((ctrl >> 1) & 7));

    volatile uint32_t *page = NULL; uint64_t addr; uint16_t data; double seconds;
    if (!apic) {
        seconds = atof(argv[3]);
        data = argc > 4 ? (uint16_t)strtoul(argv[4], 0, 0) : 0x5a5a;
        for (int tries = 0; tries < 512 && !page; tries++) {
            void *p = mmap(0, 4096, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);
            if (p == MAP_FAILED) { perror("mmap"); return 1; }
            mlock(p, 4096);
            memset(p, 0xde, 4096);
            uint64_t ph = phys_of(p);
            /* 64-bit capable MSI: any physical page. Otherwise it has to be below 4 GiB (keep trying other pages). */
            if (ph && (is64 || ph < 0xfffff000ULL)) { page = p; addr = ph; }
        }
        if (!page) { fprintf(stderr, "no usable page found (or not root: pagemap shows no PFN)\n"); return 1; }
        printf("MSI target: pinned RAM page, physical address 0x%llx (%s), expected data 0x%04x\n", (unsigned long long)addr, addr > 0xffffffffULL ? "above 4 GiB: 64-bit MSI write" : "below 4 GiB", data);
    } else {
        uint32_t vec = strtoul(argv[3], 0, 0);
        seconds = atof(argv[4]);
        addr = 0xfee00000ULL; data = (uint16_t)vec;
        printf("MSI target: local APIC 0x%llx, vector 0x%02x (edge, fixed, CPU 0)\n", (unsigned long long)addr, vec);
    }

    wr32(off_lo, (uint32_t)(addr & 0xfffffffc));
    if (is64) wr32(off_hi, (uint32_t)(addr >> 32));
    wr16(off_dat, data);
    wr16(cap + 2, (ctrl & ~0x70) | 1);                 /* one message, enable */
    printf("MSI enabled; now trigger it from the card (readback: lo 0x%08x data 0x%04x control 0x%04x)\n", rd32(off_lo), rd16(off_dat), rd16(cap + 2));
    fflush(stdout);

    double t0 = now();
    int hit = 0;
    while (now() - t0 < seconds) {
        if (page && page[0] != 0xdededede) {
            printf("MSI WRITE ARRIVED after %.2f s: word0 = 0x%08x (others untouched: %s)\n", now() - t0, page[0],
                (page[1] == 0xdededede) ? "yes" : "NO");
            hit = 1; break;
        }
        usleep(2000);
    }
    if (!hit && !apic) printf("no MSI write seen in %.0f s\n", seconds);
    if (apic) printf("waited %.0f s: check dmesg for 'No irq handler for vector'\n", seconds);

    wr16(cap + 2, ctrl & ~1);                          /* MSI disabled again, then restore address/data */
    wr32(off_lo, s_lo); if (is64) wr32(off_hi, s_hi); wr16(off_dat, s_dat);
    wr16(cap + 2, ctrl);
    printf("MSI capability restored (control 0x%04x)\n", rd16(cap + 2));
    return hit ? 0 : (apic ? 0 : 3);
}
