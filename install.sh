#!/usr/bin/env bash
# Установка студии. Работает на macOS и Linux.
set -e
cd "$(dirname "$0")"

say() { printf '\n  %s\n' "$1"; }

# --- ffmpeg и rubberband -----------------------------------------------------
# ffmpeg обязателен: через него читается и пишется весь звук.
# rubberband желателен: без него растяжка и питч считаются запасным способом,
# заметно грязнее на слух, но работать всё будет.

install_audio_tools() {
  if command -v ffmpeg >/dev/null 2>&1 && command -v rubberband >/dev/null 2>&1; then
    say "ffmpeg и rubberband уже стоят."
    return
  fi

  if command -v brew >/dev/null 2>&1; then
    say "Ставлю через Homebrew…"
    brew install ffmpeg rubberband
  elif command -v apt-get >/dev/null 2>&1; then
    say "Ставлю через apt…"
    sudo apt-get update && sudo apt-get install -y ffmpeg rubberband-cli
  elif command -v dnf >/dev/null 2>&1; then
    say "Ставлю через dnf…"
    sudo dnf install -y ffmpeg rubberband
  elif command -v pacman >/dev/null 2>&1; then
    say "Ставлю через pacman…"
    sudo pacman -S --needed --noconfirm ffmpeg rubberband
  else
    say "Не нашёл пакетный менеджер. Поставь ffmpeg и rubberband сам, потом запусти снова."
    exit 1
  fi
}

# --- python ------------------------------------------------------------------

find_python() {
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)'; then
        echo "$candidate"
        return
      fi
    fi
  done
  say "Нужен Python 3.10 или новее. Поставь его и запусти снова."
  exit 1
}

install_audio_tools
PYTHON="$(find_python)"
say "Готовлю окружение на $PYTHON…"
"$PYTHON" -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e .

say "Готово. Запуск: ./start.sh   (на Mac можно двойным кликом по «Запустить студию.command»)"
