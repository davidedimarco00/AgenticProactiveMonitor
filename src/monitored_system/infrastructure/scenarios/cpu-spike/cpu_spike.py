from __future__ import annotations

import argparse
import multiprocessing as mp
import signal
import time


def burn_cpu() -> None:
    value = 1
    while True:
        value = (value * 3 + 7) % 1_000_003


def main() -> None:
    parser = argparse.ArgumentParser(description="Controlled CPU spike for thesis experiments")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    if args.workers < 1 or args.workers > 8:
        raise SystemExit("workers must be between 1 and 8")

    workers = [mp.Process(target=burn_cpu, daemon=True) for _ in range(args.workers)]
    for worker in workers:
        worker.start()

    running = True

    def stop_handler(_signum: int, _frame: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)

    try:
        while running:
            time.sleep(1)
    finally:
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
        for worker in workers:
            worker.join(timeout=2)


if __name__ == "__main__":
    main()
