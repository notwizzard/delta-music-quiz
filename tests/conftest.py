"""Синтетические треки с заранее известными темпом и тональностью.

Проверять пайплайн на настоящей музыке нельзя — нет эталона, с чем сравнивать.
Поэтому генерируем материал сами: бочка, малый барабан и аккорды с точно
заданным темпом, и уже на нём меряем, насколько точно всё определяется.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

SR = 44100


def _kick(sr: int, duration: float = 0.18) -> np.ndarray:
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    frequency = 120 * np.exp(-t * 30) + 45
    return np.sin(2 * np.pi * np.cumsum(frequency) / sr) * np.exp(-t * 18)


def _snare(sr: int, duration: float = 0.12) -> np.ndarray:
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    return np.random.default_rng(0).normal(0, 0.3, t.size) * np.exp(-t * 30)


def _tone(sr: int, frequency: float, duration: float) -> np.ndarray:
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    envelope = np.minimum(1.0, t * 40) * np.exp(-t * 1.2)
    wave = np.sin(2 * np.pi * frequency * t) + 0.4 * np.sin(4 * np.pi * frequency * t)
    return wave * envelope * 0.25


def make_track(path: Path, bpm: float, root_hz: float, bars: int = 16, sr: int = SR) -> Path:
    """Собрать трек: бочка на каждой доле (на сильной — громче), аккорды по тактам."""
    beat = 60.0 / bpm
    buffer = np.zeros(int(bars * 4 * beat * sr) + sr)
    kick, snare = _kick(sr), _snare(sr)
    progression = [0, 9, 5, 7]  # I - vi - IV - V

    for bar in range(bars):
        semitones = progression[bar % len(progression)]
        bar_start = int(bar * 4 * beat * sr)
        for interval in (0, 4, 7):
            note = _tone(sr, root_hz * 2 ** ((semitones + interval) / 12), 4 * beat)
            buffer[bar_start:bar_start + note.size] += note

        for position in range(4):
            at = int((bar * 4 + position) * beat * sr)
            buffer[at:at + kick.size] += kick * (1.0 if position == 0 else 0.35)
            if position in (1, 3):
                buffer[at:at + snare.size] += snare * 0.6

    buffer /= np.max(np.abs(buffer)) * 1.1
    sf.write(str(path), np.stack([buffer, buffer]).T, sr)
    return path


@pytest.fixture(scope="session")
def pure_tone() -> np.ndarray:
    """Чистая синусоида 440 Гц.

    На аккордах сдвиг высоты не померить: квинта вверх почти неотличима от
    третьей гармоники исходной ноты, и любая спектральная сверка путается.
    У синусоиды пик один, и после сдвига он обязан оказаться ровно там, где надо.
    """
    t = np.linspace(0, 4.0, int(SR * 4.0), endpoint=False)
    wave = (np.sin(2 * np.pi * 440.0 * t) * 0.5).astype(np.float32)
    return np.stack([wave, wave])


@pytest.fixture(scope="session")
def tracks(tmp_path_factory) -> dict[str, tuple[Path, float]]:
    """Три трека с разным темпом и разной тоникой."""
    folder = tmp_path_factory.mktemp("fixtures")
    return {
        "alpha": (make_track(folder / "alpha.wav", 120, 261.63), 120.0),   # C
        "beta": (make_track(folder / "beta.wav", 90, 196.00), 90.0),       # G
        "gamma": (make_track(folder / "gamma.wav", 140, 220.00), 140.0),   # A
    }
