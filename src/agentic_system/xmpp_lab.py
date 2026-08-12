from __future__ import annotations

import logging
import ssl
from typing import Any

import spade.agent as spade_agent
from spade.xmpp_client import XMPPClient as SpadeXMPPClient

logger = logging.getLogger(__name__)


def _allow_unencrypted_sasl(client: Any) -> None:
    """Allow SASL only inside the isolated thesis Docker network.

    Slixmpp treats authentication policy separately from its transport flags.
    Disabling STARTTLS therefore does not automatically allow SCRAM or PLAIN on
    an unencrypted stream. The feature_mechanisms plugin must be configured too.
    """

    try:
        mechanisms = client["feature_mechanisms"]
    except Exception as exc:  # pragma: no cover - defensive compatibility path
        raise RuntimeError("Slixmpp feature_mechanisms plugin is unavailable") from exc

    for option in (
        "unencrypted_plain",
        "unencrypted_scram",
        "unencrypted_cram",
        "unencrypted_digest",
    ):
        setattr(mechanisms, option, True)

    # Some Slixmpp versions retain plugin options in a dictionary in addition
    # to exposing them as attributes.
    for attribute in ("config", "plugin_config"):
        config = getattr(mechanisms, attribute, None)
        if isinstance(config, dict):
            config.update(
                {
                    "unencrypted_plain": True,
                    "unencrypted_scram": True,
                    "unencrypted_cram": True,
                    "unencrypted_digest": True,
                }
            )


class PlaintextLabXMPPClient(SpadeXMPPClient):
    """SPADE XMPP client for the isolated thesis Docker network only."""

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

        # Slixmpp >= 1.10 transport feature flags.
        if hasattr(self, "enable_direct_tls"):
            self.enable_direct_tls = False
        if hasattr(self, "enable_starttls"):
            self.enable_starttls = False
        if hasattr(self, "enable_plaintext"):
            self.enable_plaintext = True

        # Older Slixmpp transport attributes.
        if hasattr(self, "force_starttls"):
            self.force_starttls = False
        if hasattr(self, "disable_starttls"):
            self.disable_starttls = True

        _allow_unencrypted_sasl(self)

    def connect(self, *args: Any, **kwargs: Any):
        """Connect without direct TLS or STARTTLS across Slixmpp versions."""
        if hasattr(self, "enable_direct_tls"):
            self.enable_direct_tls = False
        if hasattr(self, "enable_starttls"):
            self.enable_starttls = False
        if hasattr(self, "enable_plaintext"):
            self.enable_plaintext = True

        _allow_unencrypted_sasl(self)

        kwargs["use_ssl"] = False
        kwargs["force_starttls"] = False
        kwargs["disable_starttls"] = True

        try:
            return super().connect(*args, **kwargs)
        except TypeError:
            # Newer Slixmpp versions rely on feature flags rather than the
            # legacy keyword arguments.
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
