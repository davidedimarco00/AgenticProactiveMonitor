from __future__ import annotations

import argparse
import asyncio
import logging

from .xmpp_lab import configure_plaintext_xmpp_for_lab


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Simplified five-agent collaborative troubleshooting runtime"
    )
    parser.add_argument(
        "--config",
        default="src/agentic_system/config/agents.yaml",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)

    # The thesis stack runs on an isolated Docker network. Configure SPADE before
    # importing the runtime so all agents use plaintext XMPP without STARTTLS.
    configure_plaintext_xmpp_for_lab()

    from .simple.main import run

    asyncio.run(run(args.config))


if __name__ == "__main__":
    main()
