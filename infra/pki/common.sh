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

# Refuse a path that is, or lies under, a Git work tree. Two independent checks, both
# on the PHYSICAL path (symlinks already resolved): a '.git' entry in any ancestor, and
# Git's own answer. Git failing to answer is a refusal, never a pass: the only accepted
# outcome is Git itself saying the path is not in a repository. PKI_GIT names the git
# executable (default: git on PATH).
pki_outside_git() {
  local path=$1 probe next output status git_bin=${PKI_GIT:-git}
  probe=$path
  while :; do
    [[ ! -e "$probe/.git" ]] || fail "$path is inside a Git work tree: keys must never be near the repository"
    next=$(dirname "$probe")
    [[ "$next" != "$probe" ]] || break
    probe=$next
  done
  if command -v "$git_bin" >/dev/null 2>&1; then
    status=0
    output=$(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_COMMON_DIR LC_ALL=C "$git_bin" -C "$path" rev-parse --is-inside-work-tree 2>&1) || status=$?
    if [[ "$status" -eq 0 ]]; then
      fail "$path is inside a Git work tree: keys must never be near the repository"
    elif [[ "$status" -ne 128 || "$output" != *"not a git repository"* ]]; then
      fail "cannot establish that $path is outside a Git work tree (git exited $status): refusing"
    fi
  fi
}

# The PKI directory is created by the operator, outside any Git work tree, and is
# where every key lives. Refusing a work tree is what keeps keys out of Git.
pki_directory() {
  local dir=${1:-} physical
  [[ -n "$dir" ]] || fail "the PKI directory is required"
  [[ -d "$dir" ]] || fail "the PKI directory does not exist: $dir (create it first, outside the repository)"
  physical=$(cd "$dir" && pwd -P)
  pki_outside_git "$physical"
  echo "$physical"
}

# Create the directories under the PKI directory that hold a credential's own
# directory (<kind>/<name>), one component at a time. A symbolic link in the path is
# refused (it could point into a work tree); the result is checked physical and outside
# Git. An existing real directory is fine: that is where a new label goes.
pki_parent_directory() {
  local current=$1 part physical
  shift
  for part in "$@"; do
    current="$current/$part"
    [[ ! -L "$current" ]] || fail "refusing a symbolic link in the PKI directory: $current"
    [[ -d "$current" ]] || mkdir "$current" 2>/dev/null || [[ -d "$current" && ! -L "$current" ]] \
      || fail "cannot create $current"
    chmod 700 "$current" 2>/dev/null || true
  done
  physical=$(cd "$current" && pwd -P)
  [[ "$physical" == "$current" ]] || fail "refusing a path that is not physical: $current is $physical"
  pki_outside_git "$physical"
}

# Never overwrite: replacing a credential is a rotation, which is a new label. The
# existence test IS the creation: mkdir without -p is atomic, so of two concurrent
# issues with one label exactly one gets the directory and the other is refused (a
# separate test followed by mkdir -p let both write into it).
pki_new_directory() {
  local path=$1
  mkdir "$path" 2>/dev/null || fail "already exists, refusing to overwrite: $path"
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
