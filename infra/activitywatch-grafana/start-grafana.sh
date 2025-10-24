#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${GF_SERVER_PORT:=3030}"
export GF_PATHS_CONFIG="$ROOT/grafana.ini"
export GF_PATHS_DATA="$ROOT/data"
export GF_PATHS_LOGS="$ROOT/log"
export GF_PATHS_PROVISIONING="$ROOT/provisioning"
export GF_PATHS_PLUGINS="$ROOT/plugins"
export GF_INSTALL_PLUGINS="frser-sqlite-datasource"
exec nix shell nixpkgs#grafana -c grafana server --homepath /nix/store/8sk5sdwhhmc2gldgszjgjli222zm2zsz-grafana-12.2.0/share/grafana
