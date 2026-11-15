# Experimental ath11k RX memory measurements

Local diagnostic work on `ath11k-rx-memory-diagnostics`. Patch 961 adds the
measurement; patch 960 remains the private-cache fix, unchanged. Neither the
diagnostic nor these scripts have been pushed. Package release 16 distinguishes
this diagnostic build from the existing release 15 build.

## What it measures

Each radio exposes a root-readable `rx_memory` file beneath
`/sys/kernel/debug/ath11k/<device>/mac<index>/`. Opening it snapshots the RX,
monitor and monitor-status rings. Reading the file in chunks uses the same
snapshot. No packet contents or kernel addresses are printed.

For each ring, `backing_bytes` counts the entire compound-page allocations
reachable through ring-owned skb head fragments, deduplicated within that ring.
`unique_pages` means unique compound allocations, not a count of base pages.
Several buffers sharing one allocation count its bytes once. This measures
reachable backing, not exclusively owned memory or the number of bytes used
by packet data.

Do not add the backing totals across rings: rings are sampled at different
times and may share allocations. CE buffers, payload fragments, buffers already
handed to the network stack, and cache-only references are excluded. This is
not a total for all driver memory.

Use rows with `complete=1` for comparisons. Non-fragment buffers, invalid
entries, inconsistent orders or exceeding the 8192-buffer limit mark a row
incomplete. Empty inactive rings are normal. `generation` changes when ring
state is initialized; `start_boottime_ns` identifies snapshot time.

## Overhead and lifetime

There is no allocation tracing or per-packet accounting. A read temporarily
allocates about 128 KiB of scratch space on this target plus 4 KiB of text.
It walks each ring while holding that ring's existing IDR lock, then sorts
copied page identities outside the lock. `lock_hold_ns` reports the timed
walk, excluding lock acquisition/release overhead. `elapsed_ns` excludes
scratch allocation and output transfer.

A separate mutex serializes snapshots with ring initialization and teardown.
Reads return EBUSY during competing operations, EAGAIN when rings are not
ready, or ENOMEM if scratch allocation fails. Only formatted text survives
open; subsequent reads do not access the driver. The debugfs entry is removed
before the radio is freed. These protections do not establish that firmware
crash recovery itself works.

## Build

The local checkout includes both 960 and 961. Enable
`CONFIG_PACKAGE_MAC80211_DEBUGFS=y` (already enabled in the existing server
build). The kernel also needs debugfs. ATH11K verbose debug logging is not
required.

The server checkout has not been changed. Transfer the diagnostic patch and
package Makefile change there before building; do not assume switching a
branch transfers uncommitted files between machines. Preserve the server's
`.config` and package selections.

For an existing build directory, rebuild the mac80211 package from clean
sources so the new patch is applied, then build the image:

```bash
make defconfig
make package/kernel/mac80211/clean
make --jobs=4 V=s
```

This cleans the wireless package, not the entire toolchain/build. `--jobs=4`
allows four parallel jobs; `V=s` requests verbose build output. Do not flash
until the complete build succeeds and the intended RBR350 image is identified.

For a controlled baseline, use the same source revision, feeds, configuration
and diagnostic patch, omitting only 960. Keep release 16 in both diagnostic
builds. The patch is checked against main revision `7b46f9593e` and the
private-cache candidate based on it, not an unpinned future main.

## Collect on your computer

After flashing and confirming Wi-Fi works, take one sample first:

```bash
python3 scripts/ath11k-rx-memory/collect.py --host rbr350 --label candidate-smoke --output candidate-smoke.jsonl --samples 1
```

The SSH alias must resolve on the computer running the script and allow
noninteractive root login. A root SSH destination can be used instead of an
alias. Debugfs must be mounted on the router. A missing file is recorded as an
error, not a zero-memory measurement.

For a longer run:

```bash
python3 scripts/ath11k-rx-memory/collect.py --host rbr350 --label candidate-revision --output candidate.jsonl --interval 60
```

Logs stay on the collecting computer. Each JSON line includes the raw census,
system memory before/after, uptime, SSH status, stderr and a UTC timestamp.
Existing output files are never overwritten. Ctrl+C stops sampling. Reads
that fail are recorded and subsequent sampling continues.

Repeat the smoke test during traffic and inspect kernel logs for warnings and
traffic for disruption. Check lock durations before beginning a long run.
Compare baseline and candidate under similar traffic and uptime; retain exact
source revisions, feeds, configuration and firmware hashes with your results.
No crash injection is performed by this collector.

## Checks

```bash
python3 scripts/ath11k-rx-memory/test_census.py
```

This compiles the actual helper extracted from 961 with address/undefined
behavior sanitizers. It tests shared allocations, distinct allocations,
mixed orders, duplicates, overflow, the capacity boundary and randomized
inventories. It does not test kernel concurrency.

The changed C files are also compile-checked with the existing server's
kernel/configuration/toolchain, using temporary sources and outputs. Those
checks are not a full firmware build or an on-router test. Hardware smoke
testing and recovery testing remain outstanding.
