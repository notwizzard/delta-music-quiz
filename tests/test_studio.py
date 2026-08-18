"""Сквозная проверка студии: от загрузки трека до готового файла игры.

Идём через настоящий HTTP-клиент Flask, а не через внутренние функции, — так
проверяется ровно то, чем пользуется браузер.
"""

from __future__ import annotations

import base64
import io
import json
import re
import time

import pytest

from dmq.studio.app import create_app


@pytest.fixture
def client(tmp_path, tracks):
    app = create_app(tmp_path / "workspace")
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        test_client.workspace = tmp_path / "workspace"
        yield test_client


def wait(client, job, timeout=180):
    """Дождаться фоновой задачи и вернуть её результат."""
    deadline = time.time() + timeout
    while job["status"] == "running" and time.time() < deadline:
        time.sleep(0.15)
        job = client.get(f"/api/jobs/{job['id']}").get_json()
    assert job["status"] == "finished", f"{job['status']}: {job['message']} {job['log']}"
    return job["result"]


def upload(client, tracks, names):
    data = {
        "files": [
            (io.BytesIO(tracks[name][0].read_bytes()), f"{name}.wav")
            for name in names
        ]
    }
    response = client.post("/api/tracks", data=data, content_type="multipart/form-data")
    assert response.status_code == 200
    return wait(client, response.get_json())


def test_state_is_empty_at_start(client):
    state = client.get("/api/state").get_json()
    assert state["library"] == []
    assert state["pack"]["themes"] == []
    assert "В игре нет ни одной темы" in state["problems"]
    assert any(preset["name"] == "reverse" for preset in state["presets"])


def test_import_analyses_the_track(client, tracks):
    result = upload(client, tracks, ["alpha"])
    assert len(result["added"]) == 1

    library = client.get("/api/state").get_json()["library"]
    assert len(library) == 1
    assert library[0]["bpm"] == pytest.approx(120, abs=3)
    # У свежего трека сразу есть оригинал — его можно ставить в ответ.
    assert [variant["kind"] for variant in library[0]["variants"]] == ["source"]


def test_render_preset_creates_playable_variant(client, tracks):
    upload(client, tracks, ["alpha"])
    track = client.get("/api/state").get_json()["library"][0]

    response = client.post(
        f"/api/tracks/{track['id']}/render",
        json={"preset": "reverse", "start": 0, "length": 10},
    )
    result = wait(client, response.get_json())

    variant_id = result["variantId"]
    audio = client.get(f"/media/{variant_id}")
    assert audio.status_code == 200
    assert len(audio.data) > 1000

    library = client.get("/api/state").get_json()["library"]
    variant = next(v for v in library[0]["variants"] if v["id"] == variant_id)
    assert variant["duration"] == pytest.approx(10, abs=0.5)


def test_mashup_through_the_api(client, tracks):
    upload(client, tracks, ["alpha", "beta"])
    library = client.get("/api/state").get_json()["library"]
    ids = [track["id"] for track in library]

    response = client.post("/api/mashup", json={"trackIds": ids, "bars": 4, "repeat": 1})
    result = wait(client, response.get_json())
    assert "Наложение" in result["label"]

    # Заготовка помнит обоих участников, а не только того, к кому привязана.
    library = client.get("/api/state").get_json()["library"]
    mashup = next(v for v in library[0]["variants"] if v["kind"] == "mashup")
    assert set(mashup["sources"]) == set(ids)


def test_mashup_needs_at_least_two_tracks(client, tracks):
    upload(client, tracks, ["alpha"])
    track = client.get("/api/state").get_json()["library"][0]
    response = client.post("/api/mashup", json={"trackIds": [track["id"]]})
    assert response.status_code == 400


def build_simple_pack(client, tracks):
    """Собрать минимальную, но валидную игру: одна тема, два вопроса."""
    upload(client, tracks, ["alpha", "beta"])
    library = client.get("/api/state").get_json()["library"]

    questions = []
    for index, track in enumerate(library):
        response = client.post(
            f"/api/tracks/{track['id']}/render",
            json={"preset": "reverse", "start": 0, "length": 8},
        )
        variant_id = wait(client, response.get_json())["variantId"]
        original = next(v for v in track["variants"] if v["kind"] == "source")
        questions.append({
            "price": 100 * (index + 1),
            "variantId": variant_id,
            "answer": f"Ответ {index + 1}",
            "answerVariantId": original["id"],
            "comment": "",
        })

    response = client.put("/api/pack", json={
        "title": "Проверочная игра",
        "teams": ["Красные", "Синие"],
        "wrongAnswerPenalty": True,
        "themes": [{"title": "Задом наперёд", "questions": questions}],
    })
    return response.get_json()


def test_saving_pack_keeps_everything(client, tracks):
    saved = build_simple_pack(client, tracks)
    assert saved["problems"] == []

    question = saved["pack"]["themes"][0]["questions"][0]
    assert question["answer"] == "Ответ 1"
    assert question["variantId"] and question["answerVariantId"]


def test_old_pack_with_reveal_steps_still_loads(client, tracks):
    """Паки, сделанные до перехода на обычный плеер, обязаны открываться."""
    upload(client, tracks, ["alpha"])
    library = client.get("/api/state").get_json()["library"]
    original = next(v for v in library[0]["variants"] if v["kind"] == "source")

    response = client.put("/api/pack", json={
        "title": "Старый пак", "teams": ["А", "Б"],
        "themes": [{"title": "Т", "questions": [{
            "price": 100, "variantId": original["id"], "answer": "Ответ",
            "reveal": [2, 4, 8, 16],
        }]}],
    })
    assert response.get_json()["problems"] == []
    assert "reveal" not in response.get_json()["pack"]["themes"][0]["questions"][0]


def test_pack_reports_what_is_missing(client, tracks):
    upload(client, tracks, ["alpha"])
    response = client.put("/api/pack", json={
        "title": "Дырявая",
        "teams": ["А", "Б"],
        "themes": [{"title": "Тема", "questions": [
            {"price": 100, "variantId": "", "answer": ""}
        ]}],
    })
    problems = response.get_json()["problems"]
    assert any("не выбран звук" in problem for problem in problems)
    assert any("не заполнен ответ" in problem for problem in problems)


def test_export_produces_self_contained_file(client, tracks):
    build_simple_pack(client, tracks)

    response = client.post("/api/export")
    result = wait(client, response.get_json())
    assert result["questions"] == 2

    exported = client.workspace / "exports" / result["file"]
    html = exported.read_text(encoding="utf-8")

    # Ничего не должно подгружаться извне: ни ссылок, ни отдельных файлов.
    assert "<script src=" not in html
    assert "<link rel=\"stylesheet\"" not in html
    assert "http://" not in html and "https://" not in html

    # Данные внутри и разбираются как JSON.
    payload = json.loads(re.search(
        r'<script id="pack-data" type="application/json">(.*?)</script>', html, re.S
    ).group(1))
    assert payload["title"] == "Проверочная игра"
    assert payload["teams"] == ["Красные", "Синие"]

    question = payload["themes"][0]["questions"][0]
    assert base64.b64decode(question["audio"])[:2] in (b"ID", b"\xff\xfb", b"\xff\xf3")
    assert question["answerAudio"], "звук ответа должен быть вшит"
    assert question["duration"] == pytest.approx(8, abs=0.3)


def test_export_refuses_incomplete_pack(client, tracks):
    upload(client, tracks, ["alpha"])
    client.put("/api/pack", json={
        "title": "Дырявая", "teams": ["А", "Б"],
        "themes": [{"title": "Т", "questions": [{"price": 100, "variantId": "", "answer": ""}]}],
    })
    job = client.post("/api/export").get_json()
    deadline = time.time() + 30
    while job["status"] == "running" and time.time() < deadline:
        time.sleep(0.1)
        job = client.get(f"/api/jobs/{job['id']}").get_json()
    assert job["status"] == "failed"
    assert "не выбран звук" in job["message"]


def test_deleting_a_track_does_not_break_the_pack(client, tracks):
    build_simple_pack(client, tracks)
    library = client.get("/api/state").get_json()["library"]

    assert client.delete(f"/api/tracks/{library[0]['id']}").status_code == 200

    state = client.get("/api/state").get_json()
    # Ссылка на исчезнувший звук вычищена, а не оставлена битой.
    first = state["pack"]["themes"][0]["questions"][0]
    assert first["variantId"] == ""
    assert any("не выбран звук" in problem for problem in state["problems"])
