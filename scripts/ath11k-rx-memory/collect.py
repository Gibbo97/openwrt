#!/usr/bin/env python3
"""Sample RX census and system memory over SSH; store JSON lines on this host."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time


REMOTE = r'''
status=0
printf '@@ uptime\n'
cat /proc/uptime || status=1
printf '@@ memory_before\n'
cat /proc/meminfo || status=1
found=0
for file in /sys/kernel/debug/ath11k/*/mac*/rx_memory; do
    [ -f "$file" ] || continue
    found=1
    printf '@@ census %s\n' "$file"
    cat "$file" || status=1
done
if [ "$found" = 0 ]; then
    printf 'No rx_memory files found; check diagnostic patch, debugfs and radio initialization.\n' >&2
    status=3
fi
printf '@@ memory_after\n'
cat /proc/meminfo || status=1
exit "$status"
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="SSH alias, e.g. rbr350")
    parser.add_argument("--label", required=True, help="baseline or candidate, with tested revision")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=60, help="Seconds between samples (default: 60)")
    parser.add_argument("--samples", type=int, default=0, help="0 means until Ctrl+C")
    args = parser.parse_args()
    if args.interval < 1 or args.samples < 0 or args.host.startswith("-"):
        parser.error("Use an interval >= 1, samples >= 0, and a valid SSH host")
    # Never overwrite an earlier experiment or write logs onto the router.
    with args.output.open("x", buffering=1) as output:
        number = 0
        try:
            while args.samples == 0 or number < args.samples:
                start = time.monotonic()
                row = {"version": 1, "label": args.label, "sample": number,
                       "timestamp_utc": datetime.now(timezone.utc).isoformat()}
                try:
                    result = subprocess.run(
                        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                         args.host, "sh"], input=REMOTE, text=True,
                        capture_output=True, timeout=30)
                    row.update(returncode=result.returncode, stdout=result.stdout,
                               stderr=result.stderr)
                except subprocess.TimeoutExpired:
                    row.update(returncode=None, error="SSH sample timed out")
                row["duration_seconds"] = time.monotonic() - start
                output.write(json.dumps(row) + "\n")
                print(f"sample={number} result={row['returncode']} output={args.output}", flush=True)
                number += 1
                if args.samples and number >= args.samples:
                    break
                time.sleep(max(0, args.interval - (time.monotonic() - start)))
        except KeyboardInterrupt:
            print("Stopped; completed samples are saved.")


if __name__ == "__main__":
    main()
