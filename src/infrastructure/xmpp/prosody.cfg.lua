-- Prosody configuration for the isolated thesis Docker network.
-- SPADE agents use JIDs such as coordinator@xmpp and register automatically.

local xmpp_domain = os.getenv("XMPP_DOMAIN") or "xmpp"
local registration_value = string.lower(os.getenv("XMPP_ALLOW_REGISTRATION") or "true")
local registration_enabled =
  registration_value == "true"
  or registration_value == "1"
  or registration_value == "yes"
  or registration_value == "on"

admins = {}

daemonize = false
pidfile = "/tmp/prosody.pid"
data_path = "/var/lib/prosody"

authentication = "internal_hashed"
storage = "internal"

c2s_ports = { 5222 }
c2s_require_encryption = false

-- The lab does not need server-to-server federation.
modules_disabled = {
  "s2s";
}

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
}

-- SPADE starts agents with auto_register=True. Keep this enabled only on the
-- local Docker network and do not expose port 5222 publicly.
allow_registration = registration_enabled
min_seconds_between_registrations = 0

log = {
  {
    levels = { min = "info" };
    to = "console";
  };
}

VirtualHost(xmpp_domain)
