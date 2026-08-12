-- Prosody configuration for the isolated thesis Docker network.
-- SPADE agents use JIDs such as technical-lead@xmpp and can register automatically.
--
-- IMPORTANT:
-- This Prosody instance is reachable only inside the local Docker thesis network.
-- TLS is intentionally disabled for client-to-server traffic in this lab setup so
-- SPADE/Slixmpp does not have to trust a self-signed certificate for the synthetic
-- Docker-only domain "xmpp". Do not reuse this policy on an exposed XMPP server.

local xmpp_domain = Lua.os.getenv("XMPP_DOMAIN") or "xmpp"

local registration_value = Lua.string.lower(
  Lua.os.getenv("XMPP_ALLOW_REGISTRATION") or "true"
)

local registration_enabled =
  registration_value == "true"
  or registration_value == "1"
  or registration_value == "yes"
  or registration_value == "on"


-- =====================================================================
-- GLOBAL SETTINGS
-- =====================================================================

admins = {}

pidfile = "/tmp/prosody.pid"
data_path = "/var/lib/prosody"


-- =====================================================================
-- AUTHENTICATION / STORAGE
-- =====================================================================

authentication = "internal_hashed"
storage = "internal"


-- =====================================================================
-- CLIENT-TO-SERVER CONNECTIONS
-- =====================================================================

c2s_ports = { 5222 }

-- Local Docker thesis lab only.
-- No STARTTLS is advertised because mod_tls is not enabled below.
c2s_require_encryption = false

-- SPADE authenticates with SASL PLAIN. Since this isolated instance has TLS
-- intentionally disabled, Prosody must explicitly allow that mechanism on the
-- unencrypted Docker bridge connection.
allow_unencrypted_plain_auth = true


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
  "disco";
  "private";
  "blocklist";
  "vcard4";
  "vcard_legacy";
  "version";
  "uptime";
  "time";
  "ping";
  "register";
  "admin_shell";
}


-- =====================================================================
-- REGISTRATION
-- =====================================================================

-- SPADE agents can be started using:
--
-- await agent.start(auto_register=True)
--
-- Keep this enabled only inside the isolated Docker network.
allow_registration = registration_enabled

-- Disable registration rate limiting for the local thesis lab.
min_seconds_between_registrations = 0


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
