#!/bin/bash
# Установка для macOS — двойной клик по этому файлу.
cd "$(dirname "$0")" || exit 1
./install.sh
status=$?
echo
read -r -p "Нажми Enter, чтобы закрыть окно."
exit $status
