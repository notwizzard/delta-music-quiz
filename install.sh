#!/usr/bin/env bash
# Установка студии. Работает на macOS и Linux.
set -euo pipefail
cd "$(dirname "$0")"

say()  { printf '  %s\n' "$1"; }
step() { printf '\n== %s ==\n' "$1"; }
fail() { printf '\n  %s\n\n' "$1" >&2; exit 1; }

# --- 1. ffmpeg и rubberband --------------------------------------------------
# ffmpeg обязателен: через него читается и пишется весь звук.
# rubberband желателен: без него растяжка и питч считаются запасным способом,
# заметно грязнее на слух, но работать всё будет.

step "1 из 3. Утилиты для звука"

if command -v ffmpeg >/dev/null 2>&1 && command -v rubberband >/dev/null 2>&1; then
  say "ffmpeg и rubberband уже стоят."
elif command -v brew >/dev/null 2>&1; then
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
  fail "Не нашёл пакетный менеджер. Поставь ffmpeg и rubberband сам, потом запусти снова."
fi

command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg так и не появился. Без него студия работать не сможет."

# --- 2. Python ---------------------------------------------------------------
# Результат кладём в глобальную переменную, а не печатаем наружу: если искать
# питон через $(...), то и сообщения об ошибках уедут в подстановку, и exit
# внутри неё уронит скрипт молча — именно так и ломалась установка раньше.

step "2 из 3. Python"

PYTHON=""

pick_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      return 0
    fi
  done
  return 1
}

if ! pick_python; then
  say "Подходящего Python не нашлось — системный на macOS слишком старый."
  if command -v brew >/dev/null 2>&1; then
    say "Ставлю Python 3.12 через Homebrew, это займёт пару минут…"
    brew install python@3.12
    hash -r
    PATH="$(brew --prefix)/bin:$(brew --prefix)/opt/python@3.12/libexec/bin:$PATH"
    export PATH
    pick_python || fail "Python 3.12 поставился, но не нашёлся в PATH. Перезапусти Терминал и попробуй снова."
  else
    fail "Нужен Python 3.10 или новее. Поставь его с python.org и запусти снова."
  fi
fi

say "Использую $PYTHON ($("$PYTHON" -V 2>&1))"

# --- 3. Окружение ------------------------------------------------------------

step "3 из 3. Окружение"

if [ -d .venv ]; then
  say "Пересобираю окружение начисто…"
  rm -rf .venv
fi

"$PYTHON" -m venv .venv \
  || fail "Не удалось создать окружение. На Debian и Ubuntu поставь пакет python3-venv и запусти снова."

say "Ставлю зависимости, это займёт минуту…"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e .

.venv/bin/dmq --version >/dev/null 2>&1 || fail "Что-то пошло не так: программа установилась, но не запускается."

printf '\n  Готово. Запуск: ./start.sh\n'
printf '  На Mac можно двойным кликом по «Запустить студию.command».\n\n'
