"""Фонотека: исходные треки и все сделанные из них заготовки.

Фонотека живёт в рабочей папке и описывается одним файлом library.json.
Раскладка на диске:

    workspace/
        library.json     что откуда взялось
        pack.json        собранная игра
        sources/         оригиналы, как их принесли
        renders/         преобразованные заготовки
        exports/         готовые game.html

Смысл разделения: оригинал загружается и анализируется один раз, а заготовок из
него потом делается сколько угодно, и каждая помнит, из чего и как получена.
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import analysis as an
from . import audio as au


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Variant:
    """Одна заготовка — то, что можно поставить в вопрос."""

    id: str
    filename: str
    """Путь относительно папки renders/."""
    label: str
    """Человеческая подпись: «Задом наперёд», «Наложение: A + B»."""
    kind: str
    """preset — простое преобразование, mashup — наложение, source — без обработки."""
    duration: float
    preset: str | None = None
    sources: list[str] = field(default_factory=list)
    """Идентификаторы треков, из которых собрана заготовка."""

    @property
    def is_mashup(self) -> bool:
        return self.kind == "mashup"


@dataclass
class Track:
    """Исходный трек и всё, что из него сделали."""

    id: str
    title: str
    filename: str
    """Путь относительно папки sources/."""
    duration: float
    artist: str = ""
    bpm: float = 0.0
    key: str = ""
    hook: float = 0.0
    """Время самого энергичного места — обычно припев."""
    downbeat_confidence: float = 0.0
    variants: list[Variant] = field(default_factory=list)

    @property
    def display(self) -> str:
        return f"{self.artist} — {self.title}" if self.artist else self.title


class Library:
    """Чтение и запись фонотеки. Всё состояние — в одном json рядом с файлами."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.sources_dir = self.root / "sources"
        self.renders_dir = self.root / "renders"
        self.exports_dir = self.root / "exports"
        for folder in (self.sources_dir, self.renders_dir, self.exports_dir):
            folder.mkdir(parents=True, exist_ok=True)
        self.tracks: list[Track] = []
        self.load()

    # --- диск -------------------------------------------------------------

    @property
    def index_path(self) -> Path:
        return self.root / "library.json"

    def load(self) -> None:
        if not self.index_path.exists():
            self.tracks = []
            return
        raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        self.tracks = [
            Track(**{**item, "variants": [Variant(**v) for v in item.get("variants", [])]})
            for item in raw.get("tracks", [])
        ]

    def save(self) -> None:
        payload = {"version": 1, "tracks": [asdict(track) for track in self.tracks]}
        self.index_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # --- доступ -----------------------------------------------------------

    def track(self, track_id: str) -> Track:
        for track in self.tracks:
            if track.id == track_id:
                return track
        raise KeyError(f"Трек {track_id!r} не найден")

    def variant(self, variant_id: str) -> tuple[Track, Variant]:
        for track in self.tracks:
            for variant in track.variants:
                if variant.id == variant_id:
                    return track, variant
        raise KeyError(f"Заготовка {variant_id!r} не найдена")

    def path_of(self, variant: Variant) -> Path:
        """Файл заготовки на диске. Оригинал лежит в sources, остальное в renders."""
        base = self.sources_dir if variant.kind == "source" else self.renders_dir
        return base / variant.filename

    def source_path(self, track: Track) -> Path:
        return self.sources_dir / track.filename

    # --- изменение --------------------------------------------------------

    def add_track(self, path: str | Path, title: str = "", artist: str = "") -> Track:
        """Положить файл в фонотеку и разобрать его: темп, тональность, припев.

        Анализ делается сразу и один раз — дальше он нужен и для наложения,
        и для того, чтобы шаги раскрытия попадали на музыкальные фразы.
        """
        source = Path(path)
        stored_name = f"{new_id()}{source.suffix.lower()}"
        destination = self.sources_dir / stored_name
        shutil.copy2(source, destination)

        audio = au.load(destination)
        result = an.analyze(audio)
        hook = an.best_segment_start(audio, result, length=min(30.0, result.duration))

        track = Track(
            id=new_id(),
            title=title or source.stem,
            artist=artist,
            filename=stored_name,
            duration=result.duration,
            bpm=round(result.bpm, 1),
            key=result.key_name,
            hook=round(hook, 2),
            downbeat_confidence=round(result.downbeat_confidence, 2),
        )
        track.variants.append(
            Variant(
                id=new_id(),
                filename=stored_name,
                label="Оригинал",
                kind="source",
                duration=result.duration,
                sources=[track.id],
            )
        )

        self.tracks.append(track)
        self.save()
        return track

    def add_variant(self, track: Track, variant: Variant) -> Variant:
        track.variants.append(variant)
        self.save()
        return variant

    def remove_track(self, track_id: str) -> None:
        track = self.track(track_id)
        for variant in track.variants:
            self.path_of(variant).unlink(missing_ok=True)
        self.tracks.remove(track)
        self.save()

    def remove_variant(self, variant_id: str) -> None:
        track, variant = self.variant(variant_id)
        if variant.kind == "source":
            raise ValueError("Оригинал удаляется только вместе с треком")
        self.path_of(variant).unlink(missing_ok=True)
        track.variants.remove(variant)
        self.save()
