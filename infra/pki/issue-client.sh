#!/usr/bin/env bash
# Issue a client certificate for one principal (ADR-0072 section 4).
#
#   issue-client.sh <pki-dir> <principal-id> [label]
#
# EC P-256, valid 90 days, client authentication only. Prints the SHA-256 DER
# fingerprint in the 'sha256:<lowercase hex>' form of the principals file: that
# line is what goes into the file, with the principal's scopes. Files:
# <pki-dir>/clients/<principal-id>/<label>/client.{key,crt} (label defaults to
# the UTC date; an existing label is never overwritten). The private key is given
# to the deck and nobody else.
set -euo pipefail
umask 077
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$here/common.sh"

[[ "$#" -ge 2 && "$#" -le 3 ]] || { echo "usage: $0 <pki-dir> <principal-id> [label]" >&2; exit 2; }
pki_require_openssl
dir=$(pki_directory "$1")
principal=$2
label=${3:-$(date -u +%Y%m%d)}
pki_name "$principal"
pki_name "$label"
[[ -f "$dir/ca/ca.key" && -f "$dir/ca/ca.crt" ]] || fail "no CA in $dir/ca (run ca-init.sh first)"

target="$dir/clients/$principal/$label"
mkdir -p "$dir/clients/$principal"
pki_new_directory "$target"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

pki_request_config "$work/req.cnf" "$principal"
cat > "$work/ext.cnf" <<'EOF'
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = clientAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
EOF

pki_ec_key "$target/client.key"
openssl req -new -key "$target/client.key" -config "$work/req.cnf" -out "$work/client.csr"
openssl x509 -req -in "$work/client.csr" -CA "$dir/ca/ca.crt" -CAkey "$dir/ca/ca.key" \
  -set_serial "$(pki_serial)" -days 90 -sha256 -extfile "$work/ext.cnf" -out "$target/client.crt" 2>/dev/null
chmod 644 "$target/client.crt"

echo "principal_id=$principal"
echo "fingerprint=$(pki_fingerprint "$target/client.crt")"
echo "client_certificate=$target/client.crt"
echo "client_private_key=$target/client.key"
echo "client_valid_days=90"
