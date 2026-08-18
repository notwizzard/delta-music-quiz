"""Наложение нескольких треков друг на друга с попаданием в общий бит.

Как это работает по шагам:

1. Каждый трек анализируется — темп, сетка долей, сильные доли, тональность.
2. Выбирается общий темп: медиана темпов всех треков. Перед этим темпы
   приводятся к одной «октаве» — детектор регулярно выдаёт 85 вместо 170 или
   наоборот, и без этой поправки один трек поехал бы вдвое медленнее остальных.
3. В каждом треке берётся кусок нужной длины в тактах, начинающийся строго
   с сильной доли — по умолчанию из самого энергичного места, то есть обычно
   из припева.
4. Кусок растягивается или сжимается до общего темпа. Коэффициент считается не
   по среднему темпу трека, а по реальной длине выбранных тактов на сетке
   долей — так наложение не расползается, если внутри трека темп чуть плавает.
5. По желанию треки подтягиваются в одну тональность сдвигом высоты.
6. Слои выравниваются по громкости и складываются. Первая сильная доля у всех
   приходится на нулевую отметку, поэтому удары совпадают.

Где метод слабеет: живая музыка со свободным темпом и треки со сменой размера.
Там сетка долей плывёт, и лучше взять 4 такта вместо 8 или задать старт руками.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import analysis as an
from . import audio as au
from . import transforms as tr


@dataclass
class Layer:
    """Один трек внутри наложения — и что с ним сделали."""

    name: str
    source_bpm: float
    key_name: str
    start: float
    """Откуда взяли кусок в исходном треке, секунды."""
    time_ratio: float
    """Во сколько раз растянули. Больше 1 — замедлили."""
    semitones: float
    """На сколько полутонов сдвинули высоту ради общей тональности."""
    audio: np.ndarray = field(repr=False, default=None)
    """Готовый слой отдельно — пригодится, чтобы послушать, кто именно уехал."""


@dataclass
class MashupResult:
    audio: np.ndarray
    sr: int
    bpm: float
    key_name: str
    bars: int
    layers: list[Layer]

    def summary(self) -> str:
        head = f"{len(self.layers)} слоя · {self.bpm:.1f} BPM · {self.key_name} · {self.bars} такт(ов)"
        rows = [
            f"  {layer.name}: {layer.source_bpm:.1f} BPM {layer.key_name} "
            f"→ старт {layer.start:.1f} с, темп ×{1 / layer.time_ratio:.3f}, питч {layer.semitones:+.0f}"
            for layer in self.layers
        ]
        return "\n".join([head, *rows])


def _fold_bpm(bpm: float, reference: float) -> float:
    """Привести темп к «октаве» опорного: 85 против 170 — это один и тот же бит.

    Детекторы темпа регулярно ошибаются ровно вдвое, и без этой поправки один
    слой поехал бы в два раза медленнее остальных.
    """
    if bpm <= 0 or reference <= 0:
        return bpm
    folded = bpm
    while folded < reference / 1.41:
        folded *= 2
    while folded > reference * 1.41:
        folded /= 2
    return folded


def _segment_bounds(analysis: an.Analysis, start: float, beats_needed: int) -> tuple[float, float]:
    """Границы куска в исходном треке: начало на сильной доле, конец через N долей.

    Конец берётся по реальной сетке долей, а не по среднему темпу — тогда
    растяжка компенсирует локальные колебания темпа внутри трека.
    """
    beats = analysis.beats
    index = int(np.argmin(np.abs(beats - start))) if beats.size else 0

    end_index = index + beats_needed
    if end_index < beats.size:
        end = float(beats[end_index])
    else:
        end = start + beats_needed * analysis.beat_duration

    return float(beats[index]) if beats.size else start, end


def mashup(
    sources: list[str | Path],
    bars: int = 8,
    meter: int = 4,
    target_bpm: float | None = None,
    match_key: bool = True,
    starts: list[float | None] | None = None,
    repeat: int = 1,
    sr: int = au.SR,
) -> MashupResult:
    """Наложить 2–3 трека друг на друга так, чтобы они играли в один бит.

    bars — сколько тактов взять от каждого трека.
    target_bpm — общий темп; по умолчанию медиана темпов участников.
    match_key — подтягивать ли треки в одну тональность (обычно стоит: иначе
        получается каша из двух разных гармоний).
    starts — откуда брать кусок в каждом треке, секунды; None = автоматически
        из самого энергичного места.
    repeat — сколько раз повторить получившийся отрезок.
    """
    paths = [Path(s) for s in sources]
    if not 2 <= len(paths) <= 4:
        raise ValueError("Накладывать имеет смысл от 2 до 4 треков")
    if starts is not None and len(starts) != len(paths):
        raise ValueError("Список стартов должен совпадать по длине со списком треков")

    tracks = [au.load(path, sr=sr) for path in paths]
    analyses = [an.analyze(track, sr=sr, meter=meter) for track in tracks]

    # Общий темп. Опорным берём первый трек — под него подгоняем «октавы» остальных.
    reference_bpm = analyses[0].bpm
    folded = [_fold_bpm(a.bpm, reference_bpm) for a in analyses]
    bpm = float(target_bpm) if target_bpm else float(np.median(folded))

    # Общая тональность — по первому треку: он обычно ведущий в связке.
    reference_key = analyses[0]

    beats_needed = bars * meter
    target_length = beats_needed * 60.0 / bpm

    layers: list[Layer] = []
    rendered: list[np.ndarray] = []

    for index, (path, track, analysis, source_bpm) in enumerate(zip(paths, tracks, analyses, folded)):
        # Кусок нужной длины в тактах — в исходном темпе трека.
        wanted = beats_needed * (60.0 / source_bpm)
        requested = starts[index] if starts else None
        start = (
            an.snap_to_downbeat(requested, analysis, max_start=max(0.0, analysis.duration - wanted))
            if requested is not None
            else an.best_segment_start(track, analysis, length=wanted, sr=sr)
        )

        begin, end = _segment_bounds(analysis, start, beats_needed)
        chunk = au.slice_seconds(track, begin, max(end - begin, 1e-3), sr=sr)

        time_ratio = target_length / max(end - begin, 1e-6)
        semitones = float(analysis.semitones_to(reference_key)) if match_key else 0.0
        chunk = tr.stretch(chunk, sr=sr, time_ratio=time_ratio, semitones=semitones)

        # Rubber Band возвращает длину с точностью до нескольких сэмплов —
        # подрезаем всё под одну сетку, иначе слои поедут на последнем такте.
        chunk = _fit_length(chunk, int(round(target_length * sr)))
        chunk = au.normalize_loudness(chunk, sr=sr, target_lufs=-20.0)

        rendered.append(chunk)
        layers.append(
            Layer(
                name=path.stem,
                source_bpm=source_bpm,
                key_name=analysis.key_name,
                start=begin,
                time_ratio=time_ratio,
                semitones=semitones,
                audio=chunk,
            )
        )

    mixed = np.sum(rendered, axis=0)
    if repeat > 1:
        mixed = np.tile(mixed, (1, repeat))

    mixed = au.fade(mixed, sr=sr, fade_in=0.05, fade_out=0.3)
    mixed = au.normalize_loudness(mixed, sr=sr)

    return MashupResult(
        audio=mixed,
        sr=sr,
        bpm=bpm,
        key_name=reference_key.key_name,
        bars=bars,
        layers=layers,
    )


def _fit_length(audio: np.ndarray, length: int) -> np.ndarray:
    """Подогнать под точную длину: лишнее отрезать, недостающее добить тишиной."""
    audio = au.as_2d(audio)
    if audio.shape[1] == length:
        return audio
    if audio.shape[1] > length:
        return audio[:, :length]
    pad = np.zeros((audio.shape[0], length - audio.shape[1]), dtype=np.float32)
    return np.concatenate([audio, pad], axis=1)
