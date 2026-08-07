
-- Prosody configuration for the isolated thesis Docker network.
-- SPADE agents use JIDs such as coordinator@xmpp and can register automatically.

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

daemonize = false

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
certificates = "/etc/prosody/certs"
c2s_ports = { 5222 }

-- This is an isolated thesis Docker network.
-- Encryption is disabled to simplify SPADE/XMPP testing.
c2s_require_encryption = false
-- Thesis lab only: allow SPADE authentication without TLS
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

-- Default:
--   XMPP_DOMAIN=xmpp
--
-- Example SPADE JIDs:
--   sender@xmpp
--   receiver@xmpp
--   coordinator@xmpp
--   diagnosis@xmpp

VirtualHost(xmpp_domain)
