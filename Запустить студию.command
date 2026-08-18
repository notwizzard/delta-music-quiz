#!/bin/bash
# Двойной клик по этому файлу открывает студию сборки игры.
cd "$(dirname "$0")" || exit 1

if [ ! -x ".venv/bin/dmq" ]; then
  echo "Похоже, программа ещё не установлена."
  echo "Открой Терминал в этой папке и выполни: ./install.command"
  read -r -p "Нажми Enter, чтобы закрыть окно."
  exit 1
fi

exec .venv/bin/dmq studio
