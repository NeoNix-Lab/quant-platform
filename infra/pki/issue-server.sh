#!/usr/bin/env bash
# Issue the API server certificate (ADR-0072 section 4).
#
#   issue-server.sh <pki-dir> <name> <subject-alt-names> [label]
#
# <subject-alt-names> is the endpoint's names as openssl writes them, comma
# separated: "DNS:api.example.org,IP:192.0.2.10". EC P-256, valid 397 days,
# server authentication only. The deck verifies this certificate against the CA
# and the name it dials. Files: <pki-dir>/servers/<name>/<label>/server.{key,crt}
# (label defaults to the UTC date; an existing label is never overwritten).
set -euo pipefail
umask 077
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$here/common.sh"

[[ "$#" -ge 3 && "$#" -le 4 ]] || { echo "usage: $0 <pki-dir> <name> <subject-alt-names> [label]" >&2; exit 2; }
pki_require_openssl
dir=$(pki_directory "$1")
name=$2
sans=$3
label=${4:-$(date -u +%Y%m%d)}
pki_name "$name"
pki_name "$label"
[[ "$sans" =~ ^((DNS|IP):[A-Za-z0-9.:-]+)(,(DNS|IP):[A-Za-z0-9.:-]+)*$ ]] \
  || fail "subject-alt-names must look like DNS:host,IP:192.0.2.10"
[[ -f "$dir/ca/ca.key" && -f "$dir/ca/ca.crt" ]] || fail "no CA in $dir/ca (run ca-init.sh first)"

target="$dir/servers/$name/$label"
mkdir -p "$dir/servers/$name"
pki_new_directory "$target"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

pki_request_config "$work/req.cnf" "$name"
cat > "$work/ext.cnf" <<EOF
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = serverAuth
subjectAltName = ${sans}
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid
EOF

pki_ec_key "$target/server.key"
openssl req -new -key "$target/server.key" -config "$work/req.cnf" -out "$work/server.csr"
openssl x509 -req -in "$work/server.csr" -CA "$dir/ca/ca.crt" -CAkey "$dir/ca/ca.key" \
  -set_serial "$(pki_serial)" -days 397 -sha256 -extfile "$work/ext.cnf" -out "$target/server.crt" 2>/dev/null
chmod 644 "$target/server.crt"

echo "server_certificate=$target/server.crt"
echo "server_private_key=$target/server.key"
echo "server_valid_days=397"
