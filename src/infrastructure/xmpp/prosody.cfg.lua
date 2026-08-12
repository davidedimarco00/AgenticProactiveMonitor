-- Prosody configuration for the isolated thesis Docker network.
-- The five SPADE agents use pre-provisioned accounts on the Docker-local
-- VirtualHost "xmpp" (or XMPP_DOMAIN when overridden).
--
-- This configuration intentionally mirrors the SPADE/Prosody setup previously
-- validated in the project: STARTTLS is enabled and required, the self-signed
-- Docker-local certificate is accepted by SPADE with verify_security=False,
-- and agent accounts are provisioned before the backend starts.

local xmpp_domain = Lua.os.getenv("XMPP_DOMAIN") or "xmpp"


-- =====================================================================
-- GLOBAL SETTINGS
-- =====================================================================

admins = {}

pidfile = "/tmp/prosody.pid"
data_path = "/var/lib/prosody"
certificates = "/etc/prosody/certs"


-- =====================================================================
-- AUTHENTICATION / STORAGE
-- =====================================================================

authentication = "internal_hashed"
storage = "internal"


-- =====================================================================
-- CLIENT-TO-SERVER CONNECTIONS
-- =====================================================================

c2s_ports = { 5222 }

-- The previously validated SPADE setup uses STARTTLS. This avoids Slixmpp
-- rejecting every SASL mechanism as unsafe on an unencrypted connection.
c2s_require_encryption = true
allow_unencrypted_plain_auth = false


-- =====================================================================
-- DISABLED MODULES
-- =====================================================================

-- Server-to-server federation is not required in the thesis lab.
modules_disabled = {
  "s2s";
}


-- =====================================================================
-- ENABLED MODULES
-- =====================================================================

modules_enabled = {
  "roster";
  "saslauth";
  "tls";
  "disco";
  "private";
  "blocklist";
  "vcard4";
  "vcard_legacy";
  "version";
  "uptime";
  "time";
  "ping";
  "admin_shell";
}


-- =====================================================================
-- REGISTRATION
-- =====================================================================

-- Accounts are provisioned explicitly by the xmpp-bootstrap Docker service.
-- SPADE therefore connects with auto_register=False. Keeping in-band
-- registration disabled makes startup deterministic and keeps identity
-- provisioning separate from normal agent execution.
allow_registration = false


-- =====================================================================
-- LOGGING
-- =====================================================================

log = {
  {
    levels = { min = "info" };
    to = "console";
  };
}


-- =====================================================================
-- VIRTUAL HOST
-- =====================================================================

VirtualHost(xmpp_domain)
