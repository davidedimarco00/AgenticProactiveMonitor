#!/bin/sh
set -eu

XMPP_CONTAINER_NAME="${XMPP_CONTAINER_NAME:-agentic-xmpp}"
XMPP_DOMAIN="${XMPP_DOMAIN:-xmpp}"

provision_account() {
  user="$1"
  password="$2"
  jid="${user}@${XMPP_DOMAIN}"

  echo "Provisioning XMPP account ${jid}"

  # Remove stale accounts first so the configured password is always authoritative.
  docker exec -u root "${XMPP_CONTAINER_NAME}" \
    prosodyctl deluser "${jid}" >/dev/null 2>&1 || true

  output="$(docker exec -u root "${XMPP_CONTAINER_NAME}" \
    prosodyctl register "${user}" "${XMPP_DOMAIN}" "${password}" 2>&1)" || {
      echo "Unable to provision ${jid}: ${output}" >&2
      exit 1
    }
}

provision_account coordinator "${XMPP_COORDINATOR_PASSWORD:-coordinator}"
provision_account evidence "${XMPP_EVIDENCE_PASSWORD:-evidence}"
provision_account reasoning "${XMPP_REASONING_PASSWORD:-reasoning}"
provision_account critic "${XMPP_CRITIC_PASSWORD:-critic}"
provision_account remediation "${XMPP_REMEDIATION_PASSWORD:-remediation}"

echo "All SPADE XMPP accounts were provisioned successfully."
