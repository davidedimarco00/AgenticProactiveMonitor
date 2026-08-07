# XMPP + SPADE Quick Setup

- In `prosody.cfg.lua` use `VirtualHost "xmpp"` and enable `"saslauth"`, `"tls"`, `"register"` and `"admin_shell"`.
- Set `authentication = "internal_hashed"`, `c2s_ports = { 5222 }` and `c2s_require_encryption = true`.
- Set `certificates = "/etc/prosody/certs"` and `allow_unencrypted_plain_auth = false`.
- Generate the certificate: `prosodyctl cert generate xmpp`.
- Copy it: `cp /var/lib/prosody/xmpp.crt /etc/prosody/certs/xmpp.crt`.
- Copy the key: `cp /var/lib/prosody/xmpp.key /etc/prosody/certs/xmpp.key`.
- Fix permissions: `chown root:prosody /etc/prosody/certs/xmpp.* && chmod 640 /etc/prosody/certs/xmpp.*`.
- Verify TLS: `prosodyctl check certs`.
- Restart Prosody: `docker restart <container-prosody>`.
- Create and activate the virtualenv: `python -m venv .venv` then `.\.venv\Scripts\Activate.ps1`.
- Install SPADE: `python -m pip install spade`.
- Use JIDs such as `receiver@xmpp` and `sender@xmpp`, port `5222`, with `verify_security=False`.
- Start `python .\receiver.py`, then in another terminal run `python .\sender.py`.
- Check registered users with `prosodyctl shell user list xmpp`.
- If the receiver gets the sender message, SPADE → TLS/XMPP → Prosody is working correctly.
