#!/usr/bin/env bash
# Create the offline CA of the deck/server mutual TLS (ADR-0072 section 4).
#
#   ca-init.sh <pki-dir> [common-name]
#
# EC P-256, valid 3650 days. The CA private key (<pki-dir>/ca/ca.key, mode 0600)
# is meant to live OFFLINE: after issuing, move it off the machine that runs the
# API. Only ca.crt is published (the server's client trust anchor and the deck's
# server trust anchor).
set -euo pipefail
umask 077
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$here/common.sh"

[[ "$#" -ge 1 && "$#" -le 2 ]] || { echo "usage: $0 <pki-dir> [common-name]" >&2; exit 2; }
pki_require_openssl
dir=$(pki_directory "$1")
common_name=${2:-quant-platform-ca}
[[ "$common_name" =~ ^[A-Za-z0-9][A-Za-z0-9._\ -]{0,63}$ ]] || fail "invalid common name"

pki_new_directory "$dir/ca"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

pki_request_config "$work/ca.cnf" "$common_name" v3_ca
cat >> "$work/ca.cnf" <<'EOF'
[v3_ca]
basicConstraints = critical,CA:true,pathlen:0
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
EOF

pki_ec_key "$dir/ca/ca.key"
openssl req -new -x509 -key "$dir/ca/ca.key" -sha256 -days 3650 \
  -set_serial "$(pki_serial)" -config "$work/ca.cnf" -out "$dir/ca/ca.crt"
chmod 644 "$dir/ca/ca.crt"

echo "ca_certificate=$dir/ca/ca.crt"
echo "ca_private_key=$dir/ca/ca.key"
echo "ca_valid_days=3650"
echo "note=keep ca.key offline; publish only ca.crt"
