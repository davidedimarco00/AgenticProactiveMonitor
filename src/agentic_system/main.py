from __future__ import annotations

import argparse
import asyncio
import logging

from .simple.main import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Simplified five-agent collaborative troubleshooting runtime")
    parser.add_argument("--config", default="src/agentic_system/config/agents.yaml")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
