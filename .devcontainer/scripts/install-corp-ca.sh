#!/bin/sh
# =============================================================================
# Install whatever root CA terminates the TLS chain, if the chain doesn't
# already verify against the public trust store. Handles corporate MITM
# proxies (Dell TLS Decryption Authority, Zscaler, Netskope, etc.).
#
# Trust model: trust-on-first-run. Whatever the network presents gets
# installed. Must run as root — invoke via sudo.
# =============================================================================
set -eu

PROBE_HOST="${CORP_PROBE_HOST:-github.com:443}"

if openssl s_client -connect "$PROBE_HOST" </dev/null 2>/dev/null \
   | grep -q 'Verify return code: 0 (ok)'; then
  echo "No TLS interception detected - public chain already trusted."
  exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
  echo "REFUSING: interception detected but not running as root (try: sudo -E sh $0)" >&2
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

openssl s_client -showcerts -connect "$PROBE_HOST" </dev/null 2>/dev/null \
  | awk '/BEGIN CERT/,/END CERT/' > "$tmp/chain.pem"

if [ ! -s "$tmp/chain.pem" ]; then
  echo "REFUSING: could not retrieve a certificate chain from $PROBE_HOST" >&2
  exit 1
fi

csplit -sz -f "$tmp/c-" -b '%02d.pem' "$tmp/chain.pem" '/BEGIN CERTIFICATE/' '{*}'

root="$(ls "$tmp"/c-*.pem | tail -1)"
issuing="$(ls "$tmp"/c-*.pem | tail -2 | head -1)"

echo "Installing interception root:"
openssl x509 -in "$root" -noout -subject -fingerprint -sha1

cp "$root" /usr/local/share/ca-certificates/corp-root.crt
if [ "$issuing" != "$root" ]; then
  cp "$issuing" /usr/local/share/ca-certificates/corp-issuing.crt
fi

update-ca-certificates
