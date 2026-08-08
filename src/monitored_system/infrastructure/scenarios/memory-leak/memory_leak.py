from __future__ import annotations

import argparse
import signal
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="Controlled progressive memory growth for thesis experiments")
    parser.add_argument("--total-mb", type=int, default=512)
    parser.add_argument("--step-mb", type=int, default=32)
    parser.add_argument("--step-seconds", type=float, default=2.0)
    args = parser.parse_args()

    if args.total_mb < 64 or args.total_mb > 2048:
        raise SystemExit("total-mb must be between 64 and 2048")
    if args.step_mb < 1 or args.step_mb > args.total_mb:
        raise SystemExit("step-mb must be between 1 and total-mb")
    if args.step_seconds < 0.5 or args.step_seconds > 60:
        raise SystemExit("step-seconds must be between 0.5 and 60")

    running = True

    def stop_handler(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    blocks: list[bytearray] = []
    allocated_mb = 0

    while running and allocated_mb < args.total_mb:
        allocation_mb = min(args.step_mb, args.total_mb - allocated_mb)
        blocks.append(bytearray(allocation_mb * 1024 * 1024))
        allocated_mb += allocation_mb
        time.sleep(args.step_seconds)

    while running:
        time.sleep(1)


if __name__ == "__main__":
    main()
