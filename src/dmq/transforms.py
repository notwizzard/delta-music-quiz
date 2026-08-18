"""Базовые преобразования: скорость, высота, реверс.

Растяжкой во времени и сдвигом высоты занимается Rubber Band — у него заметно
меньше «металла» на больших сдвигах, чем у обычного фазового вокодера. Если
утилита не установлена, всё честно работает и на librosa, просто грязнее.
"""

from __future__ import annotations

import shutil

import librosa
import numpy as np
import soundfile as sf

from . import audio as au

_RUBBERBAND_HELP: str | None = None


def has_rubberband() -> bool:
    return shutil.which("rubberband") is not None


def _rubberband_supports(flag: str) -> bool:
    """Rubber Band 2 и 3 отличаются набором флагов, поэтому спрашиваем у самой утилиты."""
    global _RUBBERBAND_HELP
    if _RUBBERBAND_HELP is None:
        import subprocess

        proc = subprocess.run(["rubberband", "--help"], capture_output=True, text=True)
        _RUBBERBAND_HELP = (proc.stdout or "") + (proc.stderr or "")
    return flag in _RUBBERBAND_HELP


def _rubberband(audio: np.ndarray, sr: int, time_ratio: float, semitones: float) -> np.ndarray:
    """Прогнать звук через Rubber Band. time_ratio — во сколько раз удлинить."""
    audio = au.as_2d(audio)

    with au.tempdir() as tmp:
        src, dst = tmp / "in.wav", tmp / "out.wav"
        sf.write(str(src), audio.T, sr, subtype="FLOAT")

        cmd = ["rubberband", "--quiet"]
        if _rubberband_supports("--fine"):
            cmd.append("--fine")  # движок R3, лучшее качество
        if abs(semitones) > 1e-9:
            # Сохранение формант спасает вокал от эффекта бурундука.
            if _rubberband_supports("--formant"):
                cmd.append("--formant")
        cmd += ["--time", f"{time_ratio:.10f}", "--pitch", f"{semitones:.6f}", str(src), str(dst)]

        au.run(cmd)
        data, _ = sf.read(str(dst), dtype="float32", always_2d=True)

    return np.ascontiguousarray(data.T)


def _librosa_fallback(audio: np.ndarray, sr: int, time_ratio: float, semitones: float) -> np.ndarray:
    """Запасной путь без Rubber Band — фазовый вокодер librosa, по каналам."""
    channels = []
    for channel in au.as_2d(audio):
        out = channel
        if abs(time_ratio - 1.0) > 1e-9:
            out = librosa.effects.time_stretch(out, rate=1.0 / time_ratio)
        if abs(semitones) > 1e-9:
            out = librosa.effects.pitch_shift(out, sr=sr, n_steps=semitones)
        channels.append(out)

    length = min(c.shape[0] for c in channels)
    return np.stack([c[:length] for c in channels]).astype(np.float32)


def stretch(
    audio: np.ndarray,
    sr: int = au.SR,
    time_ratio: float = 1.0,
    semitones: float = 0.0,
) -> np.ndarray:
    """Изменить длительность и/или высоту, не трогая вторую характеристику.

    time_ratio — во сколько раз удлинить звук (2.0 = вдвое медленнее).
    semitones — сдвиг высоты в полутонах.
    """
    if abs(time_ratio - 1.0) < 1e-9 and abs(semitones) < 1e-9:
        return au.as_2d(audio)

    engine = _rubberband if has_rubberband() else _librosa_fallback
    return engine(audio, sr, time_ratio, semitones)


def speed(audio: np.ndarray, sr: int = au.SR, factor: float = 1.5) -> np.ndarray:
    """Ускорить в factor раз. Высота голоса не меняется.

    factor 1.5 — заметно быстрее, но всё ещё разборчиво; 2.0 — тараторит.
    """
    if factor <= 0:
        raise ValueError("Множитель скорости должен быть больше нуля")
    return stretch(audio, sr, time_ratio=1.0 / factor)


def slow(audio: np.ndarray, sr: int = au.SR, factor: float = 2.0) -> np.ndarray:
    """Замедлить в factor раз. Высота голоса не меняется.

    factor 2.0 — вдвое медленнее; 4.0 — трек расползается, узнать сложно.
    """
    if factor <= 0:
        raise ValueError("Множитель замедления должен быть больше нуля")
    return stretch(audio, sr, time_ratio=factor)


def pitch(audio: np.ndarray, sr: int = au.SR, semitones: float = 5.0) -> np.ndarray:
    """Сдвинуть высоту на semitones полутонов. Скорость не меняется.

    Плюс — вверх, минус — вниз. ±5..7 полутонов ломают узнаваемость сильнее
    всего: тональность уже чужая, а темп ещё родной.
    """
    return stretch(audio, sr, semitones=semitones)


def reverse(audio: np.ndarray, sr: int = au.SR) -> np.ndarray:
    """Проиграть задом наперёд."""
    return au.as_2d(audio)[:, ::-1].copy()
