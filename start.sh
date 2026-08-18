#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/dmq" ]; then
  echo
  echo "  Студия ещё не установлена."
  echo "  Выполни в этой папке:  ./install.sh"
  echo
  exit 1
fi
exec .venv/bin/dmq studio "$@"
