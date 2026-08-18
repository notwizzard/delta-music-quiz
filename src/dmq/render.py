"""Создание заготовок из треков фонотеки.

Тонкая прослойка между чистыми преобразованиями и фонотекой: вырезает фрагмент,
применяет обработку, кладёт файл на диск и регистрирует заготовку.
"""

from __future__ import annotations

from . import audio as au
from . import mashup as mx
from . import presets as ps
from .library import Library, Track, Variant, new_id

DEFAULT_CLIP = 40.0
"""Длина фрагмента по умолчанию, секунды.

Сорока секунд хватает на пять шагов раскрытия — от пары секунд до целого куплета.
"""


MIN_CLIP = 1.0
"""Короче секунды фрагмент делать бессмысленно."""


def _clip(library: Library, track: Track, start: float | None, length: float | None):
    """Вырезать фрагмент трека. По умолчанию — с самого энергичного места.

    Важно, что вырезается именно кусок оригинала, и только потом к нему
    применяется преобразование. Поэтому «задом наперёд, с 0:55, длина 40» — это
    отрезок 0:55…1:35, проигранный назад, а не вся песня наоборот с обрезкой.
    """
    duration = track.duration
    begin = track.hook if start is None else max(0.0, float(start))

    # Без этой проверки просьба начать за пределами трека молча давала секунду
    # тишины: вырезание добивает нехватку нулями, и человек получал немой вопрос.
    if begin >= duration - MIN_CLIP:
        raise ValueError(
            f"Начало {_stamp(begin)} выходит за пределы трека — он длится {_stamp(duration)}"
        )

    wanted = float(length) if length else DEFAULT_CLIP
    if wanted < MIN_CLIP:
        raise ValueError(f"Длина куска должна быть хотя бы {MIN_CLIP:.0f} секунда")

    span = min(wanted, duration - begin)
    audio = au.load(library.source_path(track))
    return au.fade(au.slice_seconds(audio, begin, span)), begin, span


def _store(library: Library, track: Track, audio, label: str, kind: str,
           preset: str | None = None, sources: list[str] | None = None) -> Variant:
    identifier = new_id()
    filename = f"{identifier}.mp3"
    au.save(library.renders_dir / filename, au.normalize_loudness(audio))
    variant = Variant(
        id=identifier,
        filename=filename,
        label=label,
        kind=kind,
        duration=round(au.duration(audio), 2),
        preset=preset,
        sources=sources or [track.id],
    )
    return library.add_variant(track, variant)


def render_clip(library: Library, track_id: str, start: float | None = None,
                length: float | None = None) -> Variant:
    """Просто вырезать фрагмент без обработки."""
    track = library.track(track_id)
    audio, begin, span = _clip(library, track, start, length)
    return _store(library, track, audio, f"Фрагмент с {_stamp(begin)}", "clip")


def render_preset(library: Library, track_id: str, preset: str, start: float | None = None,
                  length: float | None = None) -> Variant:
    """Вырезать фрагмент и применить одно из готовых преобразований."""
    track = library.track(track_id)
    audio, _, _ = _clip(library, track, start, length)
    processed = ps.apply(preset, audio)
    return _store(library, track, processed, ps.get(preset).label, "preset", preset=preset)


def render_mashup(library: Library, track_ids: list[str], bars: int = 8,
                  match_key: bool = True, repeat: int = 2) -> Variant:
    """Наложить несколько треков друг на друга с попаданием в общий бит.

    Заготовка привязывается к первому треку — он же задаёт общий темп и
    тональность, — но помнит в sources всех участников.
    """
    tracks = [library.track(identifier) for identifier in track_ids]
    result = mx.mashup(
        [library.source_path(track) for track in tracks],
        bars=bars,
        match_key=match_key,
        repeat=repeat,
    )
    label = "Наложение: " + " + ".join(track.title for track in tracks)
    return _store(
        library, tracks[0], result.audio, label, "mashup",
        sources=[track.id for track in tracks],
    )


def _stamp(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"
