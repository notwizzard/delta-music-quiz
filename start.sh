#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/dmq" ]; then
  echo "Сначала выполни ./install.sh"
  exit 1
fi
exec .venv/bin/dmq studio "$@"
