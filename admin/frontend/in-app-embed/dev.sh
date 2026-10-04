#!/bin/sh
bench="${PILOT_BENCH:-$(pilot ls | awk '/●/ { print $2; exit }')}"

embed_url() {
  pilot -b "$bench" frappe set-config -g cloud_settings_embed_url "$1"
  pilot -b "$bench" frappe --site all clear-cache
}

embed_vite_url() {
  while IFS= read -r line; do
    printf '%s\n' "$line"
    case "$line" in *Local:*) embed_url "${line##* }" && break ;; esac
  done

  cat
}

trap 'embed_url ""' EXIT
trap 'exit' INT TERM HUP

vite | embed_vite_url
