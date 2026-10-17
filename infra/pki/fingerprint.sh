#!/usr/bin/env bash
# Print the SHA-256 DER fingerprint of a certificate as 'sha256:<lowercase hex>'.
#
#   fingerprint.sh <certificate.pem>
#
# It is the exact value the API compares with the principals file (ADR-0063
# section 2: authorization is by exact fingerprint, not by CA membership).
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$here/common.sh"

[[ "$#" -eq 1 ]] || { echo "usage: $0 <certificate.pem>" >&2; exit 2; }
pki_require_openssl
[[ -f "$1" ]] || fail "no such certificate: $1"
pki_fingerprint "$1"
