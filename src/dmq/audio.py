"""Ввод-вывод и базовые операции над звуком.

Внутреннее представление звука везде одинаковое:
    numpy.ndarray, dtype=float32, форма (каналы, сэмплы), частота SR.

Декодирование и кодирование идут через ffmpeg — он читает всё (mp3, m4a, opus,
flac), в отличие от libsndfile, который спотыкается на m4a.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import pyloudnorm
import soundfile as sf

SR = 44100
"""Рабочая частота дискретизации. Всё внутри пайплайна живёт на ней."""

TARGET_LUFS = -16.0
"""Целевая громкость выдачи. Чтобы вопросы в паке не скакали по громкости."""

PEAK_CEILING_DB = -1.0
"""Потолок по пику после нормализации, дБFS."""


class ToolMissing(RuntimeError):
    """Нужная внешняя утилита не установлена."""


def require_tool(name: str, install_hint: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise ToolMissing(f"Не найдена утилита {name!r}. Установи: {install_hint}")
    return path


def run(cmd: list[str]) -> None:
    """Запустить внешнюю утилиту, при ошибке показать её stderr целиком."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tool = Path(cmd[0]).name
        raise RuntimeError(f"{tool} завершился с кодом {proc.returncode}:\n{proc.stderr}")


@contextlib.contextmanager
def tempdir():
    path = tempfile.mkdtemp(prefix="dmq-")
    try:
        yield Path(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def load(path: str | os.PathLike, sr: int = SR) -> np.ndarray:
    """Прочитать файл любого формата в (каналы, сэмплы) float32 на частоте sr."""
    require_tool("ffmpeg", "brew install ffmpeg")
    src = Path(path)
    if not src.exists():
        raise FileNotFoundError(src)

    with tempdir() as tmp:
        wav = tmp / "decoded.wav"
        run([
            "ffmpeg", "-v", "error", "-y",
            "-i", str(src),
            "-ar", str(sr),
            "-c:a", "pcm_f32le",
            str(wav),
        ])
        data, _ = sf.read(str(wav), dtype="float32", always_2d=True)

    return np.ascontiguousarray(data.T)


def save(
    path: str | os.PathLike,
    audio: np.ndarray,
    sr: int = SR,
    bitrate: str = "192k",
) -> Path:
    """Сохранить (каналы, сэмплы) в wav/mp3/ogg — формат берётся из расширения."""
    dst = Path(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    audio = as_2d(audio)

    if dst.suffix.lower() == ".wav":
        sf.write(str(dst), audio.T, sr, subtype="PCM_16")
        return dst

    require_tool("ffmpeg", "brew install ffmpeg")
    with tempdir() as tmp:
        wav = tmp / "out.wav"
        sf.write(str(wav), audio.T, sr, subtype="FLOAT")
        run([
            "ffmpeg", "-v", "error", "-y",
            "-i", str(wav),
            "-b:a", bitrate,
            str(dst),
        ])
    return dst


def as_2d(audio: np.ndarray) -> np.ndarray:
    """Привести к форме (каналы, сэмплы)."""
    arr = np.asarray(audio, dtype=np.float32)
    if arr.ndim == 1:
        return arr[np.newaxis, :]
    return arr


def to_mono(audio: np.ndarray) -> np.ndarray:
    """Схлопнуть в моно (сэмплы,) — для анализа битов и тональности."""
    return as_2d(audio).mean(axis=0)


def duration(audio: np.ndarray, sr: int = SR) -> float:
    return as_2d(audio).shape[1] / sr


def slice_seconds(audio: np.ndarray, start: float, length: float, sr: int = SR) -> np.ndarray:
    """Вырезать кусок по времени. Если конец за пределами — добивается тишиной."""
    audio = as_2d(audio)
    a = max(0, int(round(start * sr)))
    b = a + int(round(length * sr))
    chunk = audio[:, a:b]
    if chunk.shape[1] < b - a:
        pad = np.zeros((chunk.shape[0], b - a - chunk.shape[1]), dtype=np.float32)
        chunk = np.concatenate([chunk, pad], axis=1)
    return chunk


def fade(audio: np.ndarray, sr: int = SR, fade_in: float = 0.02, fade_out: float = 0.05) -> np.ndarray:
    """Мягкие края, чтобы куски не щёлкали на стыках."""
    audio = as_2d(audio).copy()
    n = audio.shape[1]

    n_in = min(int(fade_in * sr), n // 2)
    if n_in > 0:
        audio[:, :n_in] *= np.linspace(0.0, 1.0, n_in, dtype=np.float32)

    n_out = min(int(fade_out * sr), n // 2)
    if n_out > 0:
        audio[:, -n_out:] *= np.linspace(1.0, 0.0, n_out, dtype=np.float32)

    return audio


def normalize_loudness(audio: np.ndarray, sr: int = SR, target_lufs: float = TARGET_LUFS) -> np.ndarray:
    """Привести к целевой громкости по EBU R128 и подрезать пики."""
    audio = as_2d(audio)
    mono = to_mono(audio)

    # Метр требует минимум 0.4 с сигнала, иначе просто оставляем как есть.
    if mono.size < int(0.4 * sr):
        return peak_limit(audio)

    meter = pyloudnorm.Meter(sr)
    loudness = meter.integrated_loudness(mono)
    if not np.isfinite(loudness):
        return peak_limit(audio)

    gain = 10.0 ** ((target_lufs - loudness) / 20.0)
    return peak_limit(audio * gain)


def peak_limit(audio: np.ndarray, ceiling_db: float = PEAK_CEILING_DB) -> np.ndarray:
    """Опустить весь сигнал, если пик выше потолка. Без искажений, просто гейн."""
    audio = as_2d(audio)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    ceiling = 10.0 ** (ceiling_db / 20.0)
    if peak > ceiling and peak > 0:
        audio = audio * (ceiling / peak)
    return audio.astype(np.float32)
