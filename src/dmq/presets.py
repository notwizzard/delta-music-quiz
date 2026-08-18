"""Готовые пресеты преобразований.

Один пресет = одна клетка на доске. Подпись из `label` пойдёт в название темы
или в комментарий к вопросу, `hint` — подсказка по сложности, чтобы раскладывать
по стоимости: чем сильнее искажение, тем дороже клетка.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import audio as au
from . import transforms as tr


@dataclass(frozen=True)
class Preset:
    name: str
    label: str
    hint: str
    apply: Callable[[np.ndarray, int], np.ndarray]


def _preset(name: str, label: str, hint: str, fn: Callable[[np.ndarray, int], np.ndarray]) -> Preset:
    return Preset(name=name, label=label, hint=hint, apply=fn)


PRESETS: dict[str, Preset] = {
    preset.name: preset
    for preset in [
        # Ускорение — высота голоса на месте.
        _preset("speed_125", "Ускорено в 1.25 раза", "легко",
                lambda a, sr: tr.speed(a, sr, 1.25)),
        _preset("speed_150", "Ускорено в 1.5 раза", "средне",
                lambda a, sr: tr.speed(a, sr, 1.5)),
        _preset("speed_200", "Ускорено в 2 раза", "сложно",
                lambda a, sr: tr.speed(a, sr, 2.0)),

        # Замедление — высота голоса на месте.
        _preset("slow_150", "Замедлено в 1.5 раза", "легко",
                lambda a, sr: tr.slow(a, sr, 1.5)),
        _preset("slow_200", "Замедлено в 2 раза", "средне",
                lambda a, sr: tr.slow(a, sr, 2.0)),
        _preset("slow_400", "Замедлено в 4 раза", "сложно",
                lambda a, sr: tr.slow(a, sr, 4.0)),

        # Реверс.
        _preset("reverse", "Задом наперёд", "сложно",
                lambda a, sr: tr.reverse(a, sr)),

        # Высота вверх — скорость на месте.
        _preset("pitch_up_3", "Выше на 3 полутона", "легко",
                lambda a, sr: tr.pitch(a, sr, 3)),
        _preset("pitch_up_5", "Выше на 5 полутонов", "средне",
                lambda a, sr: tr.pitch(a, sr, 5)),
        _preset("pitch_up_7", "Выше на 7 полутонов", "сложно",
                lambda a, sr: tr.pitch(a, sr, 7)),

        # Высота вниз — скорость на месте.
        _preset("pitch_down_3", "Ниже на 3 полутона", "легко",
                lambda a, sr: tr.pitch(a, sr, -3)),
        _preset("pitch_down_5", "Ниже на 5 полутонов", "средне",
                lambda a, sr: tr.pitch(a, sr, -5)),
        _preset("pitch_down_7", "Ниже на 7 полутонов", "сложно",
                lambda a, sr: tr.pitch(a, sr, -7)),
    ]
}

DEFAULT_SET = ["speed_150", "slow_200", "reverse", "pitch_up_5", "pitch_down_5"]
"""Базовый набор — по одному представителю каждого типа."""


def get(name: str) -> Preset:
    try:
        return PRESETS[name]
    except KeyError:
        known = ", ".join(sorted(PRESETS))
        raise KeyError(f"Неизвестный пресет {name!r}. Доступны: {known}") from None


def apply(name: str, audio: np.ndarray, sr: int = au.SR) -> np.ndarray:
    """Применить пресет и выровнять громкость результата."""
    result = get(name).apply(au.as_2d(audio), sr)
    return au.normalize_loudness(result, sr=sr)
