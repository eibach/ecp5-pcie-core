/* BAR0 register stress without DMA (run as root, driver unloaded), for hosts without python.
 *
 *   bar0_stress <resource0> <seconds> [scratch_offset] [mem_offset] [mem_words]
 *
 * Phases, all single 32-bit accesses through the sysfs resource file (uncached):
 *   1. read latency:   1,000,000 reads of the scratch register.
 *   2. write+readback: pseudo-random words written to the scratch register and read back, compared, for <seconds>.
 *      Also reads the identifier memory (mem_words words at mem_offset) after every 16th write and compares it with the
 *      first read (a read-only region: any difference is corruption on the read path).
 * Prints operations, rates and the number of mismatches. Exit status 0 only without any mismatch.
 * Defaults match the Versa bring-up design with the analyzer: scratch at 0x804, identifier memory at 0x1000 (64 words).
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <time.h>
#include <sys/mman.h>

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: %s <resource0> <seconds> [scratch_offset] [mem_offset] [mem_words]\n", argv[0]); return 2; }
    double seconds    = atof(argv[2]);
    uint32_t scratch  = argc > 3 ? strtoul(argv[3], 0, 0) : 0x804;
    uint32_t mem      = argc > 4 ? strtoul(argv[4], 0, 0) : 0x1000;
    uint32_t mem_words = argc > 5 ? strtoul(argv[5], 0, 0) : 64;

    int fd = open(argv[1], O_RDWR | O_SYNC);
    if (fd < 0) { perror(argv[1]); return 1; }
    size_t size = 0x20000;
    volatile uint32_t *bar = mmap(0, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (bar == MAP_FAILED) { perror("mmap"); return 1; }
    volatile uint32_t *sc = bar + scratch / 4;

    uint32_t original = *sc;

    /* 1. Read latency. */
    const int n_lat = 1000000;
    double t0 = now();
    uint32_t sink = 0;
    for (int i = 0; i < n_lat; i++) sink += *sc;
    double t1 = now();
    printf("read latency: %.0f ns per read (%d reads, %.2f Mreads/s)%s\n", (t1 - t0) / n_lat * 1e9, n_lat, n_lat / (t1 - t0) / 1e6, sink == 0 ? "" : "");

    /* Reference copy of the identifier memory. */
    uint32_t *ref = malloc(mem_words * 4);
    for (uint32_t i = 0; i < mem_words; i++) ref[i] = bar[mem / 4 + i];

    /* 2. Write + readback, with periodic memory compare. */
    uint64_t writes = 0, mism = 0, mem_reads = 0, mem_mism = 0;
    uint32_t x = 0x12345678;
    double t2 = now(), last = t2;
    while (now() - t2 < seconds) {
        for (int k = 0; k < 4096; k++) {
            x ^= x << 13; x ^= x >> 17; x ^= x << 5;       /* xorshift32 */
            *sc = x;
            uint32_t r = *sc;
            writes++;
            if (r != x) {
                mism++;
                if (mism <= 5) printf("MISMATCH write %08x read %08x (op %llu, %.1f s)\n", x, r, (unsigned long long)writes, now() - t2);
            }
            if ((k & 15) == 0) {
                for (uint32_t i = 0; i < mem_words; i++) {
                    uint32_t v = bar[mem / 4 + i];
                    mem_reads++;
                    if (v != ref[i]) { mem_mism++; if (mem_mism <= 5) printf("MEM MISMATCH word %u: %08x expected %08x (%.1f s)\n", i, v, ref[i], now() - t2); }
                }
            }
        }
        if (now() - last > 10) { last = now(); printf("  %.0f s: %llu write+readback, %llu mismatches, %llu memory words, %llu memory mismatches\n", now() - t2, (unsigned long long)writes, (unsigned long long)mism, (unsigned long long)mem_reads, (unsigned long long)mem_mism); fflush(stdout); }
    }
    double t3 = now();
    *sc = original;
    printf("write+readback: %llu operations in %.1f s (%.2f Mops/s, %.0f ns per write+read), %llu mismatches\n",
        (unsigned long long)writes, t3 - t2, writes / (t3 - t2) / 1e6, (t3 - t2) / writes * 1e9, (unsigned long long)mism);
    printf("identifier memory: %llu words read, %llu mismatches\n", (unsigned long long)mem_reads, (unsigned long long)mem_mism);
    printf("RESULT: %s\n", (mism == 0 && mem_mism == 0) ? "OK" : "FAILED");
    return (mism == 0 && mem_mism == 0) ? 0 : 1;
}
