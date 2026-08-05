from __future__ import annotations

import logging
import ssl
from typing import Any

import spade.agent as spade_agent
from spade.xmpp_client import XMPPClient as SpadeXMPPClient

logger = logging.getLogger(__name__)


class PlaintextLabXMPPClient(SpadeXMPPClient):
    """SPADE XMPP client configured for the isolated thesis Docker network.

    TLS is intentionally disabled because Prosody is reachable only inside the
    local Docker network and uses a development self-signed certificate.
    """

    def __init__(
        self,
        jid: Any,
        password: str,
        verify_security: bool,
        auto_register: bool,
    ) -> None:
        super().__init__(jid, password, False, auto_register)

        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE

        # Slixmpp >= 1.10 uses feature flags.
        if hasattr(self, "enable_direct_tls"):
            self.enable_direct_tls = False
        if hasattr(self, "enable_starttls"):
            self.enable_starttls = False
        if hasattr(self, "enable_plaintext"):
            self.enable_plaintext = True

        # Older Slixmpp releases use these attributes.
        if hasattr(self, "force_starttls"):
            self.force_starttls = False
        if hasattr(self, "disable_starttls"):
            self.disable_starttls = True

    def connect(self, *args: Any, **kwargs: Any):
        """Connect without direct TLS or STARTTLS across Slixmpp versions."""
        if hasattr(self, "enable_direct_tls"):
            self.enable_direct_tls = False
        if hasattr(self, "enable_starttls"):
            self.enable_starttls = False
        if hasattr(self, "enable_plaintext"):
            self.enable_plaintext = True

        kwargs["use_ssl"] = False
        kwargs["force_starttls"] = False
        kwargs["disable_starttls"] = True

        try:
            return super().connect(*args, **kwargs)
        except TypeError:
            # Newer Slixmpp versions may rely only on feature flags.
            kwargs.pop("use_ssl", None)
            kwargs.pop("force_starttls", None)
            kwargs.pop("disable_starttls", None)
            return super().connect(*args, **kwargs)


def configure_plaintext_xmpp_for_lab() -> None:
    """Install the local-lab XMPP client before SPADE agents are created."""
    spade_agent.XMPPClient = PlaintextLabXMPPClient
    logger.warning(
        "XMPP TLS and certificate verification are disabled for the isolated thesis lab"
    )
