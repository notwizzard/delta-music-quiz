"""HTTP-слой студии: отдаёт страницу и обслуживает её запросы.

Приложение сугубо локальное и однопользовательское — оно поднимается на
127.0.0.1 и живёт ровно столько, сколько открыто окно. Поэтому здесь нет ни
аутентификации, ни сессий: единственный клиент — браузер того же человека.
"""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory

from .. import exporter, presets, render
from ..library import Library
from ..pack import DEFAULT_PRICES, Pack, Question, Theme
from .jobs import Job, JobRegistry

AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".aac", ".wma"}


def create_app(workspace: str | Path) -> Flask:
    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)

    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 ГБ на пачку загрузки

    library = Library(root)
    pack_path = root / "pack.json"
    pack = Pack.load(pack_path)
    jobs = JobRegistry()
    lock = threading.Lock()

    # ------------------------------------------------------------- страница

    @app.get("/")
    def index():
        return send_from_directory(app.template_folder, "studio.html")

    # ------------------------------------------------------------ состояние

    def library_payload() -> list[dict]:
        return [
            {
                "id": track.id,
                "title": track.title,
                "artist": track.artist,
                "display": track.display,
                "duration": round(track.duration, 1),
                "bpm": track.bpm,
                "key": track.key,
                "hook": track.hook,
                "shakyBeat": track.downbeat_confidence < 1.2,
                "variants": [
                    {
                        "id": variant.id,
                        "label": variant.label,
                        "kind": variant.kind,
                        "preset": variant.preset,
                        "duration": round(variant.duration, 1),
                        "sources": variant.sources,
                    }
                    for variant in track.variants
                ],
            }
            for track in library.tracks
        ]

    def pack_payload() -> dict:
        return {
            "title": pack.title,
            "teams": pack.teams,
            "wrongAnswerPenalty": pack.wrong_answer_penalty,
            "themes": [
                {
                    "title": theme.title,
                    "questions": [
                        {
                            "price": question.price,
                            "variantId": question.variant_id,
                            "answer": question.answer,
                            "answerVariantId": question.answer_variant_id,
                            "comment": question.comment,
                        }
                        for question in theme.questions
                    ],
                }
                for theme in pack.themes
            ],
        }

    @app.get("/api/state")
    def state():
        known = {variant.id for track in library.tracks for variant in track.variants}
        return jsonify({
            "library": library_payload(),
            "pack": pack_payload(),
            "presets": [
                {"name": name, "label": preset.label, "hint": preset.hint}
                for name, preset in presets.PRESETS.items()
            ],
            "prices": DEFAULT_PRICES,
            "problems": pack.problems(known),
            "workspace": str(root),
        })

    # -------------------------------------------------------------- импорт

    @app.post("/api/tracks")
    def import_tracks():
        uploads = request.files.getlist("files")
        accepted = [
            item for item in uploads
            if item.filename and Path(item.filename).suffix.lower() in AUDIO_SUFFIXES
        ]
        if not accepted:
            return jsonify({"error": "Не выбрано ни одного аудиофайла"}), 400

        # Читаем содержимое сразу: поток запроса закроется, как только вернём ответ.
        staged: list[tuple[str, bytes]] = [(item.filename, item.read()) for item in accepted]
        inbox = root / ".inbox"
        inbox.mkdir(exist_ok=True)

        def work(job: Job):
            added = []
            for name, blob in staged:
                job.log.append(f"Разбираю {name}")
                temporary = inbox / name
                temporary.write_bytes(blob)
                try:
                    with lock:
                        track = library.add_track(temporary, title=Path(name).stem)
                    added.append(track.id)
                except Exception as error:  # noqa: BLE001
                    job.log.append(f"  не вышло: {error}")
                finally:
                    temporary.unlink(missing_ok=True)
                    job.done += 1
            return {"added": added}

        return jsonify(jobs.start("Загрузка треков", len(staged), work).as_dict())

    @app.get("/api/jobs/<job_id>")
    def job_status(job_id: str):
        job = jobs.get(job_id)
        return (jsonify(job.as_dict()), 200) if job else (jsonify({"error": "нет такой задачи"}), 404)

    @app.delete("/api/tracks/<track_id>")
    def delete_track(track_id: str):
        with lock:
            library.remove_track(track_id)
            _drop_missing_questions()
        return jsonify({"ok": True})

    # ---------------------------------------------------------- преобразования

    @app.post("/api/tracks/<track_id>/render")
    def render_variant(track_id: str):
        body = request.get_json(force=True)
        preset = body.get("preset")
        start = body.get("start")
        length = body.get("length")

        def work(job: Job):
            with lock:
                if preset:
                    variant = render.render_preset(library, track_id, preset, start, length)
                else:
                    variant = render.render_clip(library, track_id, start, length)
            return {"variantId": variant.id, "label": variant.label}

        return jsonify(jobs.start("Готовлю заготовку", 1, work).as_dict())

    @app.post("/api/mashup")
    def make_mashup():
        body = request.get_json(force=True)
        track_ids = body.get("trackIds") or []
        if not 2 <= len(track_ids) <= 4:
            return jsonify({"error": "Для наложения нужно от 2 до 4 треков"}), 400

        def work(job: Job):
            with lock:
                variant = render.render_mashup(
                    library,
                    track_ids,
                    bars=int(body.get("bars", 8)),
                    match_key=bool(body.get("matchKey", True)),
                    repeat=int(body.get("repeat", 2)),
                )
            return {"variantId": variant.id, "label": variant.label}

        return jsonify(jobs.start("Накладываю треки", 1, work).as_dict())

    @app.delete("/api/variants/<variant_id>")
    def delete_variant(variant_id: str):
        with lock:
            try:
                library.remove_variant(variant_id)
            except ValueError as error:
                return jsonify({"error": str(error)}), 400
            _drop_missing_questions()
        return jsonify({"ok": True})

    @app.get("/media/<variant_id>")
    def media(variant_id: str):
        try:
            _, variant = library.variant(variant_id)
        except KeyError:
            return jsonify({"error": "нет такой заготовки"}), 404
        return send_file(library.path_of(variant), conditional=True)

    # ----------------------------------------------------------------- пак

    @app.put("/api/pack")
    def save_pack():
        nonlocal pack
        body = request.get_json(force=True)
        themes = []
        for theme in body.get("themes", []):
            questions = []
            for item in theme.get("questions", []):
                questions.append(Question(
                    price=int(item.get("price", 100)),
                    variant_id=item.get("variantId") or "",
                    answer=item.get("answer", ""),
                    answer_variant_id=item.get("answerVariantId") or None,
                    comment=item.get("comment", ""),
                ))
            themes.append(Theme(title=theme.get("title", ""), questions=questions))

        with lock:
            pack = Pack(
                title=body.get("title") or "Музыкальная своя игра",
                teams=body.get("teams") or ["Команда 1", "Команда 2"],
                themes=themes,
                wrong_answer_penalty=bool(body.get("wrongAnswerPenalty", True)),
            )
            pack.save(pack_path)

        known = {variant.id for track in library.tracks for variant in track.variants}
        return jsonify({"pack": pack_payload(), "problems": pack.problems(known)})

    def _drop_missing_questions() -> None:
        """Убрать из пака ссылки на исчезнувшие заготовки, чтобы игра не ломалась."""
        known = {variant.id for track in library.tracks for variant in track.variants}
        for theme in pack.themes:
            for question in theme.questions:
                if question.variant_id not in known:
                    question.variant_id = ""
                if question.answer_variant_id and question.answer_variant_id not in known:
                    question.answer_variant_id = None
        pack.save(pack_path)

    # ------------------------------------------------------------- экспорт

    @app.post("/api/export")
    def export_game():
        safe = "".join(character for character in pack.title if character.isalnum() or character in " -_").strip()
        destination = library.exports_dir / f"{safe or 'igra'}.html"

        def work(job: Job):
            result = exporter.export(pack, library, destination)
            return {
                "file": result.path.name,
                "sizeMb": result.size_mb,
                "questions": result.questions,
                "clips": result.clips,
            }

        return jsonify(jobs.start("Собираю игру", 1, work).as_dict())

    @app.get("/api/download/<path:name>")
    def download(name: str):
        target = library.exports_dir / name
        if not target.exists():
            return jsonify({"error": "файл не найден"}), 404
        return send_file(target, as_attachment=True)

    return app


def _studio_already_running(url: str) -> bool:
    """Отличить свою же запущенную студию от чужой программы на том же порту."""
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(url + "api/state", timeout=1.5) as response:
            return "library" in json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, ValueError, TimeoutError, OSError):
        return False


def _port_is_free(host: str, port: int) -> bool:
    import socket

    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def serve(workspace: str | Path, host: str = "127.0.0.1", port: int = 4321, open_browser: bool = True) -> None:
    """Поднять студию и открыть её в браузере.

    Сервер запускается через make_server, а не через app.run(), по одной
    причине: app.run() печатает предупреждение про development server и лог
    каждого запроса. Человеку, который просто ведёт викторину, красная надпись
    в чёрном окне выглядит как поломка, поэтому в окне остаётся только адрес,
    путь к рабочей папке и способ всё это закрыть.

    Занятый порт проверяется заранее и вручную: Werkzeug в этом случае сам
    печатает английское сообщение и завершает процесс через SystemExit, так что
    перехватить это и объяснить по-человечески уже поздно.
    """
    import logging

    from werkzeug.serving import make_server

    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    url = f"http://{host}:{port}/"

    if not _port_is_free(host, port):
        if _studio_already_running(url):
            print(f"\n  Студия уже запущена, открываю её: {url}\n", flush=True)
            if open_browser:
                webbrowser.open(url)
            return
        raise SystemExit(
            f"\n  Порт {port} занят другой программой.\n"
            f"  Запусти студию на другом порту: dmq studio --port {port + 1}\n"
        )

    app = create_app(workspace)

    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    print(f"\n  Студия открыта: {url}", flush=True)
    print(f"  Рабочая папка:  {Path(workspace).expanduser().resolve()}\n", flush=True)
    print("  Чтобы закрыть студию, нажми Ctrl+C в этом окне.\n", flush=True)

    server = make_server(host, port, app, threaded=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Студия закрыта. Всё сохранено.\n", flush=True)
    finally:
        server.server_close()
