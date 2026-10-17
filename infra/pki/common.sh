#!/usr/bin/env bash
# Shared helpers of the infra/pki scripts (ADR-0072 section 4). Sourced, never executed.
#
# Within ADR-0063 section 2: certificates and a fingerprint file, no credential
# service. Private keys are files outside the repository (mode 0600): they never
# enter Git, an environment variable or a unit file.

fail() { echo "pki: FATAL: $*" >&2; exit 1; }

pki_require_openssl() {
  command -v openssl >/dev/null 2>&1 || fail "openssl is required"
}

# A principal, server or label name: a path component, never a path.
pki_name() {
  [[ "${1:-}" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || fail "invalid name '${1:-}' (letters, digits, '.', '_' and '-')"
}

# The PKI directory is created by the operator, outside any Git work tree, and is
# where every key lives. Refusing a work tree is what keeps keys out of Git.
pki_directory() {
  local dir=${1:-}
  [[ -n "$dir" ]] || fail "the PKI directory is required"
  [[ -d "$dir" ]] || fail "the PKI directory does not exist: $dir (create it first, outside the repository)"
  if git -C "$dir" rev-parse --show-toplevel >/dev/null 2>&1; then
    fail "the PKI directory is inside a Git work tree: keys must never be near the repository"
  fi
  (cd "$dir" && pwd)
}

# Never overwrite: replacing a credential is a rotation, which is a new label.
pki_new_directory() {
  local path=$1
  [[ ! -e "$path" ]] || fail "already exists, refusing to overwrite: $path"
  mkdir -p "$path"
  chmod 700 "$path"
}

# A 128-bit random serial, as the '0x...' form the openssl commands take.
pki_serial() { printf '0x%s' "$(openssl rand -hex 16)"; }

# SHA-256 of the DER certificate, in the sha256:<lowercase hex> form of the principals file.
pki_fingerprint() {
  openssl x509 -in "$1" -noout -fingerprint -sha256 \
    | sed 's/^.*=//' | tr -d ':' | tr 'A-F' 'a-f' | sed 's/^/sha256:/'
}

# Write a minimal request configuration (no dependency on the system openssl.cnf).
pki_request_config() {
  local file=$1 common_name=$2 extensions=${3:-}
  {
    echo "[req]"
    echo "distinguished_name = dn"
    echo "prompt = no"
    [[ -z "$extensions" ]] || echo "x509_extensions = ${extensions}"
    echo "[dn]"
    echo "CN = ${common_name}"
  } > "$file"
}

pki_ec_key() {
  openssl ecparam -name prime256v1 -genkey -noout -out "$1"
  chmod 600 "$1"
}
