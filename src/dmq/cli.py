"""Командная строка dmq.

    dmq presets                      список доступных преобразований
    dmq analyze track.mp3            темп, тональность, сетка долей
    dmq transform track.mp3 --all    все преобразования одного трека
    dmq batch tracks/ -o out/        то же самое по всей папке
    dmq mashup a.mp3 b.mp3 c.mp3     наложение с попаданием в бит
    dmq studio                       студия сборки игры в браузере
    dmq export                       собрать игру из рабочей папки
"""

from __future__ import annotations

from pathlib import Path

import click

from . import analysis as an
from . import audio as au
from . import mashup as mx
from . import presets as ps
from .pack import Pack

AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma"}


def _collect(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in AUDIO_SUFFIXES)


def _prepare(path: Path, clip: float | None, start: str, sr: int):
    """Загрузить трек и, если просили, вырезать из него фрагмент.

    start='auto' — взять самое энергичное место, то есть обычно припев.
    """
    track = au.load(path, sr=sr)
    if clip is None:
        return track

    if start == "auto":
        analysis = an.analyze(track, sr=sr)
        begin = an.best_segment_start(track, analysis, length=clip, sr=sr)
    else:
        begin = float(start)

    return au.fade(au.slice_seconds(track, begin, clip, sr=sr), sr=sr)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="dmq")
def cli() -> None:
    """Преобразования музыки для викторины «Угадай мелодию»."""


@cli.command("presets")
def presets_cmd() -> None:
    """Показать все доступные преобразования."""
    width = max(len(name) for name in ps.PRESETS)
    for name, preset in ps.PRESETS.items():
        mark = "*" if name in ps.DEFAULT_SET else " "
        click.echo(f"{mark} {name:<{width}}  {preset.label:<26} [{preset.hint}]")
    click.echo("\n* — входит в базовый набор (--default)")


@cli.command("analyze")
@click.argument("sources", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
def analyze_cmd(sources: tuple[Path, ...]) -> None:
    """Показать темп, тональность и сильные доли трека."""
    for path in sources:
        result = an.analyze(str(path))
        first = f"{result.downbeats[0]:.2f} с" if result.downbeats.size else "не найдена"
        click.echo(
            f"{path.name}\n"
            f"  темп             {result.bpm:.1f} BPM\n"
            f"  тональность      {result.key_name} (уверенность {result.key_confidence:.2f})\n"
            f"  длина            {result.duration:.1f} с\n"
            f"  долей            {result.beats.size}, сильных {result.downbeats.size}\n"
            f"  первая сильная   {first} (контраст {result.downbeat_confidence:.2f})"
        )
        if result.downbeats_are_shaky:
            click.secho("  ! фаза такта найдена неуверенно — проверь наложение ушами", fg="yellow")


@cli.command("transform")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
@click.option("--preset", "-p", "chosen", multiple=True, help="Имя пресета, можно несколько раз.")
@click.option("--all", "use_all", is_flag=True, help="Применить все пресеты.")
@click.option("--default", "use_default", is_flag=True, help="Применить базовый набор.")
@click.option("--out", "-o", type=click.Path(path_type=Path), default=Path("out"), show_default=True)
@click.option("--clip", type=float, default=None, help="Длина фрагмента в секундах (по умолчанию весь трек).")
@click.option("--start", default="auto", show_default=True, help="Начало фрагмента: 'auto' или секунды.")
@click.option("--format", "fmt", default="mp3", show_default=True, type=click.Choice(["mp3", "wav", "ogg"]))
def transform_cmd(
    source: Path,
    chosen: tuple[str, ...],
    use_all: bool,
    use_default: bool,
    out: Path,
    clip: float | None,
    start: str,
    fmt: str,
) -> None:
    """Прогнать один трек через выбранные преобразования."""
    names = _resolve_presets(chosen, use_all, use_default)
    track = _prepare(source, clip, start, au.SR)

    out.mkdir(parents=True, exist_ok=True)
    au.save(out / f"{source.stem}__original.{fmt}", au.normalize_loudness(track))
    click.echo(f"{source.stem}__original.{fmt}")

    for name in names:
        result = ps.apply(name, track)
        destination = out / f"{source.stem}__{name}.{fmt}"
        au.save(destination, result)
        click.echo(f"{destination.name}  ({ps.get(name).label})")


@cli.command("batch")
@click.argument("folder", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--preset", "-p", "chosen", multiple=True)
@click.option("--all", "use_all", is_flag=True)
@click.option("--default", "use_default", is_flag=True)
@click.option("--out", "-o", type=click.Path(path_type=Path), default=Path("out"), show_default=True)
@click.option("--clip", type=float, default=30.0, show_default=True, help="Длина фрагмента в секундах.")
@click.option("--start", default="auto", show_default=True)
@click.option("--format", "fmt", default="mp3", show_default=True, type=click.Choice(["mp3", "wav", "ogg"]))
def batch_cmd(
    folder: Path,
    chosen: tuple[str, ...],
    use_all: bool,
    use_default: bool,
    out: Path,
    clip: float | None,
    start: str,
    fmt: str,
) -> None:
    """Прогнать все треки из папки. Результат ложится в подпапки по трекам."""
    names = _resolve_presets(chosen, use_all, use_default)
    tracks = _collect(folder)
    if not tracks:
        raise click.ClickException(f"В {folder} нет аудиофайлов")

    click.echo(f"{len(tracks)} трек(ов) × {len(names)} преобразований\n")
    for path in tracks:
        click.echo(path.name)
        try:
            track = _prepare(path, clip, start, au.SR)
            destination = out / path.stem
            destination.mkdir(parents=True, exist_ok=True)
            au.save(destination / f"original.{fmt}", au.normalize_loudness(track))
            for name in names:
                au.save(destination / f"{name}.{fmt}", ps.apply(name, track))
            click.echo(f"  готово → {destination}")
        except Exception as error:  # один битый файл не должен ронять весь прогон
            click.echo(f"  ошибка: {error}", err=True)


@cli.command("mashup")
@click.argument("sources", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--out", "-o", type=click.Path(path_type=Path), default=None, help="Куда сохранить.")
@click.option("--bars", type=int, default=8, show_default=True, help="Сколько тактов взять.")
@click.option("--meter", type=int, default=4, show_default=True, help="Долей в такте.")
@click.option("--bpm", type=float, default=None, help="Общий темп; по умолчанию медиана.")
@click.option("--no-key-match", is_flag=True, help="Не подтягивать треки в одну тональность.")
@click.option("--start", "starts", multiple=True, help="Начало куска для каждого трека: секунды или 'auto'.")
@click.option("--repeat", type=int, default=1, show_default=True, help="Повторить отрезок N раз.")
def mashup_cmd(
    sources: tuple[Path, ...],
    out: Path | None,
    bars: int,
    meter: int,
    bpm: float | None,
    no_key_match: bool,
    starts: tuple[str, ...],
    repeat: int,
) -> None:
    """Наложить 2–4 трека друг на друга, синхронизировав по битам."""
    if starts and len(starts) != len(sources):
        raise click.ClickException("Стартов должно быть столько же, сколько треков")

    parsed = [None if s == "auto" else float(s) for s in starts] if starts else None
    destination = out or Path("out") / ("mashup__" + "__".join(p.stem[:20] for p in sources) + ".mp3")

    result = mx.mashup(
        list(sources),
        bars=bars,
        meter=meter,
        target_bpm=bpm,
        match_key=not no_key_match,
        starts=parsed,
        repeat=repeat,
    )

    au.save(destination, result.audio, sr=result.sr)
    click.echo(result.summary())
    click.echo(f"→ {destination}")


def _resolve_presets(chosen: tuple[str, ...], use_all: bool, use_default: bool) -> list[str]:
    """--all, потом явный список, иначе базовый набор."""
    if use_all:
        return list(ps.PRESETS)
    if chosen:
        for name in chosen:
            ps.get(name)  # ранняя проверка имени, чтобы не падать в середине прогона
        return list(chosen)
    return list(ps.DEFAULT_SET)


DEFAULT_WORKSPACE = "~/Documents/Музыкальная игра"


def _workspace(path: str) -> Path:
    return Path(path).expanduser()


@cli.command("studio")
@click.option("--workspace", "-w", default=DEFAULT_WORKSPACE, show_default=True,
              help="Папка, где хранятся музыка и собранные игры.")
@click.option("--port", "-p", default=4321, show_default=True)
@click.option("--no-browser", is_flag=True, help="Не открывать браузер самому.")
def studio_cmd(workspace: str, port: int, no_browser: bool) -> None:
    """Открыть студию сборки игры в браузере."""
    from .studio import serve

    serve(_workspace(workspace), port=port, open_browser=not no_browser)


@cli.command("export")
@click.option("--workspace", "-w", default=DEFAULT_WORKSPACE, show_default=True)
@click.option("--out", "-o", type=click.Path(path_type=Path), default=None,
              help="Куда сохранить готовый файл игры.")
def export_cmd(workspace: str, out: Path | None) -> None:
    """Собрать игру из рабочей папки в один самодостаточный HTML-файл."""
    from . import exporter
    from .library import Library

    root = _workspace(workspace)
    library = Library(root)
    pack = Pack.load(root / "pack.json")
    destination = out or library.exports_dir / "igra.html"

    try:
        result = exporter.export(pack, library, destination)
    except exporter.ExportError as error:
        raise click.ClickException(str(error)) from None

    click.echo(f"{result.path}\n  вопросов: {result.questions}, звуков: {result.clips}, размер: {result.size_mb} МБ")


if __name__ == "__main__":
    cli()
