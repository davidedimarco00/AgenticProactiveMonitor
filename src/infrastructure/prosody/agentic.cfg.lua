-- Local thesis-lab configuration.
-- The XMPP service is reachable only inside the isolated Docker network.
-- TLS and certificate validation are intentionally disabled for this lab.

allow_registration = true
authentication = "internal_hashed"

c2s_require_encryption = false
allow_unencrypted_plain_auth = true

s2s_require_encryption = false
s2s_secure_auth = false
