#!/usr/bin/env python3
"""Compile and exercise the actual aggregation helper extracted from patch 961."""

import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


TEST = r'''
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include "debugfs_rx_mem.h"

static struct ath11k_rx_mem_totals sum(struct ath11k_rx_mem_page *p,
                                     unsigned int n, unsigned int shift)
{
    qsort(p, n, sizeof(*p), ath11k_rx_mem_cmp);
    return ath11k_rx_mem_sum(p, n, shift);
}

int main(void)
{
    struct ath11k_rx_mem_page pages[8192];
    struct ath11k_rx_mem_totals t;
    unsigned int i, run;
    unsigned int seed = 12345;

    t = sum(pages, 0, 12);
    assert(t.unique_pages == 0 && t.backing_bytes == 0);

    /* Same buffer inventory, packed vs one order-3 page per buffer. */
    for (i = 0; i < 4095; i++)
        pages[i] = (struct ath11k_rx_mem_page){i / 12 + 1, 3};
    t = sum(pages, 4095, 12);
    assert(t.unique_pages == 342 && t.backing_bytes == 11206656);
    assert(t.higher_order_pages == 342 && t.order0_pages == 0);
    for (i = 0; i < 4095; i++)
        pages[i].head = i + 1;
    t = sum(pages, 4095, 12);
    assert(t.unique_pages == 4095 && t.backing_bytes == 134184960);

    /* Base-page fallback, duplicate heads, and unsigned address ordering. */
    pages[0] = (struct ath11k_rx_mem_page){ULONG_MAX, 3};
    pages[1] = (struct ath11k_rx_mem_page){1, 0};
    pages[2] = pages[0];
    t = sum(pages, 3, 12);
    assert(pages[0].head == 1);
    assert(t.unique_pages == 2 && t.backing_bytes == 36864);
    assert(t.order0_pages == 1 && t.higher_order_pages == 1);
    t = sum(pages, 3, 16);
    assert(t.backing_bytes == 589824);

    /* Detect inconsistent identities and avoid undefined shifts/overflow. */
    pages[0] = (struct ath11k_rx_mem_page){1, 0};
    pages[1] = (struct ath11k_rx_mem_page){1, 3};
    t = sum(pages, 2, 12);
    assert(t.inconsistent_pages == 1);
    pages[0] = (struct ath11k_rx_mem_page){1, 64};
    t = sum(pages, 1, 12);
    assert(t.inconsistent_pages == 1 && t.backing_bytes == 0);
    t = sum(pages, 1, 64);
    assert(t.inconsistent_pages == 1 && t.backing_bytes == 0);
    pages[0] = (struct ath11k_rx_mem_page){1, 51};
    pages[1] = (struct ath11k_rx_mem_page){2, 51};
    t = sum(pages, 2, 12);
    assert(t.inconsistent_pages == 1 && t.backing_bytes == (1ULL << 63));

    /* Maximum snapshot size: every entry shares a single backing page. */
    for (i = 0; i < 8192; i++)
        pages[i] = (struct ath11k_rx_mem_page){123, 3};
    t = sum(pages, 8192, 12);
    assert(t.unique_pages == 1 && t.backing_bytes == 32768);

    /* Compare mixed populations against independent set accounting. */
    for (run = 0; run < 200; run++) {
        unsigned char seen[128] = {0};
        unsigned int unique = 0, small = 0;
        unsigned long long bytes = 0;
        for (i = 0; i < 256; i++) {
            unsigned int key, order;
            seed = seed * 1664525u + 1013904223u;
            key = (seed >> 16) % 128;
            order = key % 4;
            pages[i] = (struct ath11k_rx_mem_page){key + 1, order};
            if (!seen[key]) {
                seen[key] = 1;
                unique++;
                small += order == 0;
                bytes += 4096ULL << order;
            }
        }
        t = sum(pages, 256, 12);
        assert(t.unique_pages == unique && t.backing_bytes == bytes);
        assert(t.order0_pages == small);
        assert(t.higher_order_pages == unique - small);
        assert(t.inconsistent_pages == 0);
    }
    puts("PASS: empty, packed/scattered, mixed orders, deduplication, 64K pages,");
    puts("      unsigned addresses, invalid data, overflow, capacity, 200 populations");
}
'''


def main():
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patch", type=Path, default=repo / "package/kernel/mac80211/patches/ath11k/961-wifi-ath11k-add-diagnostic-rx-memory-census.patch")
    parser.add_argument("--compiler", default=shutil.which("clang") or shutil.which("cc"))
    args = parser.parse_args()
    if not args.compiler:
        parser.error("A host C compiler is required")
    header = []
    active = False
    for line in args.patch.read_text().splitlines():
        if line.startswith("--- "):
            active = False
        elif line.startswith("+++ "):
            active = line.endswith("/debugfs_rx_mem.h")
        elif active and line.startswith("+"):
            header.append(line[1:])
    if not header:
        parser.error("Could not extract the added debugfs_rx_mem.h from the patch")
    with tempfile.TemporaryDirectory(prefix="ath11k-rx-memory-test-") as directory:
        root = Path(directory)
        (root / "linux").mkdir()
        (root / "linux/types.h").write_text("#include <stdint.h>\ntypedef uint64_t u64;\ntypedef uint32_t u32;\n")
        (root / "debugfs_rx_mem.h").write_text("\n".join(header) + "\n")
        (root / "test.c").write_text(TEST)
        subprocess.run([args.compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-fsanitize=address,undefined", "-I", str(root), str(root / "test.c"), "-o", str(root / "test")], check=True)
        subprocess.run([str(root / "test")], check=True)


if __name__ == "__main__":
    main()
