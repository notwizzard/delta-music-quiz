"""Сборка готовой игры в один самодостаточный HTML-файл.

Почему именно один файл со звуком внутри. Игру ведут с чужого ноутбука, часто
без интернета и без права что-либо устанавливать. Папка с файлами ломается при
пересылке, локальный сервер требует терминала, а веб-версия требует сети. Один
html открывается двойным кликом на любой системе, где есть браузер, и работает
целиком офлайн.

Плата за это — размер: звук лежит внутри в base64, что добавляет к нему треть.
Поэтому перед упаковкой каждая заготовка пережимается в моно-mp3 с невысоким
битрейтом: для викторины этого хватает с запасом, а файл получается втрое легче.
"""

from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import audio as au
from .library import Library
from .pack import Pack

WEB_DIR = Path(__file__).resolve().parent / "web" / "game"

EXPORT_BITRATE = "96k"
"""Битрейт упаковки. Моно 96k на слух неотличимо для угадайки и втрое легче исходника."""

IMAGE_MAX_WIDTH = 1400
"""Во сколько пикселей ужимать картинки. Больше проектору всё равно не нужно."""

IMAGE_QUALITY = 4
"""Качество JPEG в шкале ffmpeg: 2 — почти без потерь, 31 — мыло."""

_ALPHA_PIXEL_FORMATS = ("rgba", "bgra", "argb", "abgr", "ya", "yuva", "pal8")


class ExportError(RuntimeError):
    pass


@dataclass
class ExportResult:
    path: Path
    size_bytes: int
    questions: int
    clips: int

    @property
    def size_mb(self) -> float:
        return round(self.size_bytes / 1024 / 1024, 1)


def _read(name: str) -> str:
    path = WEB_DIR / name
    if not path.exists():
        raise ExportError(f"Не найден шаблон игры: {path}")
    return path.read_text(encoding="utf-8")


def _encode_clip(source: Path, cache: dict[Path, str]) -> str:
    """Пережать заготовку и вернуть её в base64. Повторы считаются один раз."""
    if source in cache:
        return cache[source]
    if not source.exists():
        raise ExportError(f"Пропал файл заготовки: {source.name}")

    with au.tempdir() as tmp:
        compressed = tmp / "clip.mp3"
        au.run([
            "ffmpeg", "-v", "error", "-y",
            "-i", str(source),
            "-ac", "1",
            "-b:a", EXPORT_BITRATE,
            str(compressed),
        ])
        encoded = base64.b64encode(compressed.read_bytes()).decode("ascii")

    cache[source] = encoded
    return encoded


def _has_transparency(source: Path) -> bool:
    """Есть ли у картинки прозрачность — от этого зависит формат упаковки."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt", "-of", "csv=p=0", str(source)],
        capture_output=True, text=True,
    )
    pixel_format = probe.stdout.strip().lower()
    return any(marker in pixel_format for marker in _ALPHA_PIXEL_FORMATS)


def _encode_image(source: Path, cache: dict[Path, str]) -> str:
    """Ужать картинку и вернуть готовый data-URI.

    Прозрачные картинки уходят в PNG, остальные — в JPEG: он в разы легче, а
    для обложек и фотографий разницы на глаз нет. WebP был бы лучше обоих, но
    его поддержка есть не в каждой сборке ffmpeg, а зависеть от неё не хочется.
    """
    if source in cache:
        return cache[source]
    if not source.exists():
        raise ExportError(f"Пропал файл картинки: {source.name}")

    transparent = _has_transparency(source)
    suffix, codec, mime = (".png", "png", "image/png") if transparent else (".jpg", "mjpeg", "image/jpeg")

    with au.tempdir() as tmp:
        compressed = tmp / f"image{suffix}"
        command = [
            "ffmpeg", "-v", "error", "-y",
            "-i", str(source),
            "-vf", f"scale='min({IMAGE_MAX_WIDTH},iw)':-2",
            "-frames:v", "1",
            "-c:v", codec,
        ]
        if not transparent:
            command += ["-q:v", str(IMAGE_QUALITY)]
        command.append(str(compressed))

        au.run(command)
        encoded = base64.b64encode(compressed.read_bytes()).decode("ascii")

    cache[source] = f"data:{mime};base64,{encoded}"
    return cache[source]


def build_payload(pack: Pack, library: Library) -> tuple[dict, int]:
    """Превратить пак со ссылками в самодостаточные данные со звуком внутри."""
    cache: dict[Path, str] = {}
    image_cache: dict[Path, str] = {}
    themes = []

    for theme in pack.themes:
        questions = []
        for question in theme.questions:
            track, variant = library.variant(question.variant_id)
            audio_path = library.path_of(variant)
            entry = {
                "price": question.price,
                "answer": question.answer,
                "comment": question.comment,
                "duration": round(variant.duration, 2),
                "audioKey": question.variant_id,
                "audio": _encode_clip(audio_path, cache),
            }

            if question.image_id:
                image = library.image(question.image_id)
                entry["image"] = _encode_image(library.image_path(image), image_cache)
                entry["imageWhen"] = question.image_when

            if question.answer_variant_id:
                _, answer_variant = library.variant(question.answer_variant_id)
                entry["answerAudioKey"] = question.answer_variant_id
                entry["answerAudio"] = _encode_clip(library.path_of(answer_variant), cache)
                entry["answerDuration"] = round(min(answer_variant.duration, 30.0), 2)

            questions.append(entry)
        themes.append({"title": theme.title, "questions": questions})

    payload = {
        "title": pack.title,
        "teams": pack.teams,
        "wrongAnswerPenalty": pack.wrong_answer_penalty,
        "themes": themes,
    }
    return payload, len(cache)


def export(pack: Pack, library: Library, destination: str | Path) -> ExportResult:
    """Собрать игру в один файл. Перед сборкой пак проверяется на дыры."""
    known = {variant.id for track in library.tracks for variant in track.variants}
    problems = pack.problems(known, {image.id for image in library.images})
    if problems:
        raise ExportError("Игра пока не готова:\n— " + "\n— ".join(problems))

    payload, clips = build_payload(pack, library)

    # В JSON внутри <script> опасна только последовательность </, всё остальное
    # безопасно: base64 не содержит угловых скобок.
    encoded = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")

    html = (
        _read("game.html")
        .replace("__TITLE__", pack.title)
        .replace("__CSS__", _read("game.css"))
        .replace("__PACK_JSON__", encoded)
        .replace("__JS__", _read("game.js"))
    )

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")

    return ExportResult(
        path=target,
        size_bytes=target.stat().st_size,
        questions=sum(len(theme["questions"]) for theme in payload["themes"]),
        clips=clips,
    )
