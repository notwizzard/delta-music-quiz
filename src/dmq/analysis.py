"""Анализ трека: темп, сетка долей, сильные доли, тональность, самый «жирный» кусок.

Всё это нужно только для наложения треков друг на друга. Простым
преобразованиям (ускорение, реверс, питч) анализ не требуется.

Сильные доли (первая доля такта) считаются эвристикой, а не отдельной моделью:
librosa даёт ровную сетку долей, а фазу такта мы выбираем по тому, на какой из
вариантов приходится больше энергии в низах — то есть по бочке. На танцевальном
материале это работает почти всегда, на живой музыке со свободным ритмом может
промахнуться, поэтому фазу можно задать руками.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import librosa
import numpy as np

from . import audio as au

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Профили Крумхансл — Шмуклер: усреднённая «важность» каждой ступени.
_KS_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_KS_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


@dataclass
class Analysis:
    """Результат анализа одного трека."""

    bpm: float
    beats: np.ndarray = field(repr=False)
    """Времена всех долей, секунды."""
    downbeats: np.ndarray = field(repr=False)
    """Времена сильных долей (первая доля каждого такта), секунды."""
    meter: int
    """Размер: сколько долей в такте."""
    key: int
    """Тоника, 0 = C … 11 = B."""
    is_minor: bool
    key_confidence: float
    downbeat_confidence: float
    """Насколько уверенно найдена фаза такта. Ниже 1.2 — стоит проверить ушами."""
    duration: float

    @property
    def downbeats_are_shaky(self) -> bool:
        return self.downbeat_confidence < 1.2

    @property
    def key_name(self) -> str:
        return f"{NOTE_NAMES[self.key]}{'m' if self.is_minor else ''}"

    @property
    def beat_duration(self) -> float:
        return 60.0 / self.bpm

    @property
    def bar_duration(self) -> float:
        return self.beat_duration * self.meter

    def semitones_to(self, other: "Analysis") -> int:
        """На сколько полутонов сдвинуть этот трек, чтобы попасть в тональность other.

        Выбирается кратчайший путь: сдвиг всегда в пределах -6..+6 полутонов,
        чтобы трек не улетал на октаву.
        """
        delta = (other.key - self.key) % 12
        return delta - 12 if delta > 6 else delta


TEMPO_RANGE = (70.0, 160.0)
"""Диапазон, в который загоняется найденный темп.

Детектор регулярно ошибается ровно вдвое — слышит 60 там, где 120. Поскольку
половинный и двойной темп одинаково «правильны» с точки зрения сетки, мы
приводим результат к обычному человеческому диапазону, а сетку долей при этом
дробим или прореживаем на тот же множитель.
"""


def analyze(
    source: np.ndarray | str,
    sr: int = au.SR,
    meter: int = 4,
    downbeat_phase: int | None = None,
) -> Analysis:
    """Разобрать трек на темп, сетку долей и тональность.

    source — либо уже загруженный массив, либо путь к файлу.
    downbeat_phase — если задан (0..meter-1), фаза такта берётся принудительно,
    без эвристики. Пригодится, когда автоопределение промахнулось.
    """
    arr = au.load(source, sr=sr) if isinstance(source, str) else au.as_2d(source)
    y = au.to_mono(arr)

    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr, units="frames", trim=False)
    bpm = float(np.atleast_1d(tempo)[0])
    beats = librosa.frames_to_time(beat_frames, sr=sr)

    if beats.size < meter:
        # Слишком короткий или слишком ровный фрагмент — сетку строим от нуля.
        step = 60.0 / bpm if bpm > 0 else 0.5
        beats = np.arange(0.0, len(y) / sr, step)
    else:
        # Темп надёжнее считать по самой сетке, чем брать оценку детектора.
        bpm = 60.0 / float(np.median(np.diff(beats)))

    factor = _tempo_factor(bpm)
    bpm *= factor
    beats = _rescale_grid(beats, factor)

    if downbeat_phase is not None:
        phase, downbeat_confidence = downbeat_phase % meter, float("inf")
    else:
        phase, downbeat_confidence = _detect_downbeat_phase(y, sr, beats, meter)
    downbeats = beats[phase::meter]

    key, is_minor, confidence = _detect_key(y, sr)

    return Analysis(
        bpm=bpm,
        beats=beats,
        downbeats=downbeats,
        meter=meter,
        key=key,
        is_minor=is_minor,
        key_confidence=confidence,
        downbeat_confidence=downbeat_confidence,
        duration=len(y) / sr,
    )


def _tempo_factor(bpm: float, low: float = TEMPO_RANGE[0], high: float = TEMPO_RANGE[1]) -> float:
    """Во сколько раз домножить темп, чтобы он попал в человеческий диапазон."""
    if bpm <= 0:
        return 1.0
    factor = 1.0
    while bpm * factor < low:
        factor *= 2
    while bpm * factor > high:
        factor /= 2
    return factor


def _rescale_grid(beats: np.ndarray, factor: float) -> np.ndarray:
    """Согласовать сетку долей с поправкой темпа.

    Удвоили темп — значит, между соседними долями надо вставить промежуточные;
    вдвое уменьшили — наоборот, оставить каждую вторую.
    """
    if abs(factor - 1.0) < 1e-9 or beats.size < 2:
        return beats

    if factor > 1:
        parts = int(round(factor))
        dense = [
            start + (end - start) * step / parts
            for start, end in zip(beats[:-1], beats[1:])
            for step in range(parts)
        ]
        dense.append(float(beats[-1]))
        return np.asarray(dense)

    return beats[:: int(round(1 / factor))]


def _detect_downbeat_phase(y: np.ndarray, sr: int, beats: np.ndarray, meter: int) -> tuple[int, float]:
    """Выбрать, какая доля такта — первая, по энергии бочки.

    Берём спектральный поток только по низам (до 200 Гц) — это практически чистая
    бочка — и смотрим, на какой фазе такта он в среднем сильнее.

    Важно, что поток считается по линейной амплитуде, а не в децибелах:
    логарифм сжимает динамику, и разница между сильной и слабой долей падает
    с двукратной до пары процентов, после чего фаза выбирается почти наугад.

    Возвращает фазу и контраст — во сколько раз лучшая фаза выигрывает у средней.
    Контраст около 1.0 означает, что уверенности нет и фазу лучше задать руками.
    """
    spectrum = np.abs(librosa.stft(y))
    frequencies = librosa.fft_frequencies(sr=sr, n_fft=2 * (spectrum.shape[0] - 1))
    low_band = spectrum[frequencies < 200.0]
    if low_band.shape[0] == 0:
        low_band = spectrum[:4]

    onset = librosa.onset.onset_strength(S=low_band, sr=sr)
    times = librosa.frames_to_time(np.arange(len(onset)), sr=sr)
    strength = np.interp(beats, times, onset, left=0.0, right=0.0)

    scores = [float(strength[p::meter].mean()) if strength[p::meter].size else 0.0 for p in range(meter)]
    average = float(np.mean(scores))
    contrast = max(scores) / average if average > 0 else 1.0
    return int(np.argmax(scores)), contrast


def _detect_key(y: np.ndarray, sr: int) -> tuple[int, bool, float]:
    """Тональность по Крумхансл — Шмуклер: корреляция хромаграммы с профилями."""
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    profile = chroma.mean(axis=1)
    if not np.any(profile):
        return 0, False, 0.0

    best = (0, False, -2.0)
    for tonic in range(12):
        rotated = np.roll(profile, -tonic)
        for is_minor, reference in ((False, _KS_MAJOR), (True, _KS_MINOR)):
            score = float(np.corrcoef(rotated, reference)[0, 1])
            if score > best[2]:
                best = (tonic, is_minor, score)

    return best[0], best[1], best[2]


def best_segment_start(
    source: np.ndarray | str,
    analysis: Analysis,
    length: float,
    sr: int = au.SR,
) -> float:
    """Найти время начала самого энергичного куска нужной длины.

    Это дешёвый способ попасть в припев: считаем скользящую громкость и берём
    окно с максимальной средней энергией, потом подтягиваем начало к ближайшей
    сильной доле, чтобы кусок начинался «с раз».
    """
    arr = au.load(source, sr=sr) if isinstance(source, str) else au.as_2d(source)
    y = au.to_mono(arr)

    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    window = max(1, int(length * sr / hop))

    if window >= rms.size:
        start = 0.0
    else:
        cumulative = np.concatenate([[0.0], np.cumsum(rms)])
        sums = cumulative[window:] - cumulative[:-window]
        start = float(np.argmax(sums) * hop / sr)

    return snap_to_downbeat(start, analysis, max_start=max(0.0, analysis.duration - length))


def snap_to_downbeat(time: float, analysis: Analysis, max_start: float | None = None) -> float:
    """Подтянуть момент времени к ближайшей сильной доле."""
    candidates = analysis.downbeats
    if max_start is not None:
        candidates = candidates[candidates <= max_start + 1e-6]
    if candidates.size == 0:
        return 0.0
    return float(candidates[int(np.argmin(np.abs(candidates - time)))])
