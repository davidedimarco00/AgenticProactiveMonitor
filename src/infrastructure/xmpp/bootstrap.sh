#!/bin/sh
set -eu

XMPP_DOMAIN="${XMPP_DOMAIN:-xmpp}"
CERT_DIR="/etc/prosody/certs"
CERT_FILE="${CERT_DIR}/${XMPP_DOMAIN}.crt"
KEY_FILE="${CERT_DIR}/${XMPP_DOMAIN}.key"

mkdir -p "${CERT_DIR}" /var/lib/prosody

# The thesis XMPP domain is Docker-local, so a public CA cannot issue a useful
# certificate for it. Generate a persistent self-signed certificate whose CN
# and SAN match the Prosody VirtualHost. SPADE is configured with
# verify_security=False, so certificate trust is intentionally not enforced in
# this isolated lab network, while STARTTLS is still used for SASL.
if [ ! -s "${CERT_FILE}" ] || [ ! -s "${KEY_FILE}" ]; then
  echo "[xmpp-bootstrap] Generating TLS certificate for ${XMPP_DOMAIN}..."
  openssl req \
    -x509 \
    -newkey rsa:2048 \
    -sha256 \
    -days 3650 \
    -nodes \
    -keyout "${KEY_FILE}" \
    -out "${CERT_FILE}" \
    -subj "/CN=${XMPP_DOMAIN}" \
    -addext "subjectAltName=DNS:${XMPP_DOMAIN}"
fi

# Prosody runs as the prosody user in the official image.
chown -R prosody:prosody "${CERT_DIR}" /var/lib/prosody
chmod 640 "${KEY_FILE}"
chmod 644 "${CERT_FILE}"

register_agent() {
  localpart="$1"
  password="$2"
  echo "[xmpp-bootstrap] Provisioning ${localpart}@${XMPP_DOMAIN}..."
  prosodyctl register "${localpart}" "${XMPP_DOMAIN}" "${password}"
}

register_agent "technical-lead" "${XMPP_TECHNICAL_LEAD_PASSWORD:?missing XMPP_TECHNICAL_LEAD_PASSWORD}"
register_agent "system-engineer" "${XMPP_SYSTEM_ENGINEER_PASSWORD:?missing XMPP_SYSTEM_ENGINEER_PASSWORD}"
register_agent "network-engineer" "${XMPP_NETWORK_ENGINEER_PASSWORD:?missing XMPP_NETWORK_ENGINEER_PASSWORD}"
register_agent "application-engineer" "${XMPP_APPLICATION_ENGINEER_PASSWORD:?missing XMPP_APPLICATION_ENGINEER_PASSWORD}"
register_agent "software-developer" "${XMPP_SOFTWARE_DEVELOPER_PASSWORD:?missing XMPP_SOFTWARE_DEVELOPER_PASSWORD}"

echo "[xmpp-bootstrap] TLS certificate and five SPADE accounts are ready."
