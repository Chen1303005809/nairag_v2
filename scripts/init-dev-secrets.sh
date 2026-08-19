#!/usr/bin/env bash
set -euo pipefail

secrets_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/secrets"
mkdir -p "$secrets_dir"
umask 077

create_if_missing() {
  local path="$1"
  shift
  if [[ ! -s "$path" ]]; then
    "$@" > "$path"
    chmod 600 "$path"
    printf 'created %s\n' "$path"
  else
    printf 'kept %s\n' "$path"
  fi
}

create_if_missing "$secrets_dir/postgres_password.txt" openssl rand -hex 32
create_if_missing "$secrets_dir/jwt_signing_key.txt" openssl rand -base64 48
create_if_missing "$secrets_dir/initial_admin_password.txt" openssl rand -hex 24
