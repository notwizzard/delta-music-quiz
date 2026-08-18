#!/bin/bash
# Двойной клик по этому файлу открывает студию сборки игры.
cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/dmq" ]; then
  echo
  echo "  Студия ещё не установлена."
  echo "  Закрой это окно и запусти двойным кликом «install.command» из этой же папки."
  echo
  read -r -p "Нажми Enter, чтобы закрыть окно."
  exit 1
fi

exec .venv/bin/dmq studio
