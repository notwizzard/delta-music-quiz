"""Модель игрового пака: темы, клетки, ответы и шаги раскрытия.

Пак не хранит звук — только ссылки на заготовки из фонотеки. Звук подставляется
один раз, на экспорте. Благодаря этому пак остаётся крошечным текстовым файлом,
его можно переигрывать, править и хранить в истории.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEFAULT_PRICES = [100, 200, 300, 400, 500]


@dataclass
class Question:
    """Одна клетка на доске."""

    price: int
    variant_id: str
    """Что играем — идентификатор заготовки из фонотеки."""
    answer: str = ""
    """Текст правильного ответа. Виден только ведущему, пока он не раскроет."""
    answer_variant_id: str | None = None
    """Что играем после раскрытия. Обычно оригинал трека."""
    comment: str = ""
    """Необязательная подсказка ведущему: год, факт, что сказать вслух."""
    image_id: str | None = None
    """Картинка к вопросу, если нужна."""
    image_when: str = "answer"
    """Когда показывать картинку залу: answer — вместе с ответом, question — сразу."""

    @classmethod
    def from_dict(cls, raw: dict) -> "Question":
        """Собрать вопрос, молча пропустив незнакомые поля.

        Старые паки хранили шаги раскрытия — сейчас в игре обычный плеер с
        перемоткой, и такие файлы должны продолжать открываться.
        """
        known = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in raw.items() if key in known})


@dataclass
class Theme:
    title: str
    questions: list[Question] = field(default_factory=list)


@dataclass
class Pack:
    title: str = "Музыкальная своя игра"
    teams: list[str] = field(default_factory=lambda: ["Команда 1", "Команда 2"])
    themes: list[Theme] = field(default_factory=list)
    wrong_answer_penalty: bool = True
    """Снимать ли стоимость клетки за неверный ответ."""

    # --- диск -------------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path) -> "Pack":
        file = Path(path)
        if not file.exists():
            return cls()
        raw = json.loads(file.read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "Pack":
        themes = [
            Theme(
                title=theme.get("title", ""),
                questions=[Question.from_dict(question) for question in theme.get("questions", [])],
            )
            for theme in raw.get("themes", [])
        ]
        return cls(
            title=raw.get("title", "Музыкальная своя игра"),
            teams=raw.get("teams") or ["Команда 1", "Команда 2"],
            themes=themes,
            wrong_answer_penalty=raw.get("wrong_answer_penalty", True),
        )

    def save(self, path: str | Path) -> None:
        file = Path(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

    # --- проверки ---------------------------------------------------------

    def problems(self, known_variants: set[str], known_images: set[str] | None = None) -> list[str]:
        """Что мешает сыграть эту игру. Пустой список — можно экспортировать."""
        issues: list[str] = []

        if not self.themes:
            issues.append("В игре нет ни одной темы")
        if len(self.teams) < 2:
            issues.append("Нужно минимум две команды")

        for theme in self.themes:
            if not theme.title.strip():
                issues.append("У одной из тем не заполнено название")
            if not theme.questions:
                issues.append(f"В теме «{theme.title}» нет ни одного вопроса")

            for question in theme.questions:
                where = f"«{theme.title}» за {question.price}"
                if not question.variant_id:
                    issues.append(f"{where}: не выбран звук")
                elif question.variant_id not in known_variants:
                    issues.append(f"{where}: выбранный звук пропал из фонотеки")
                if not question.answer.strip():
                    issues.append(f"{where}: не заполнен ответ")
                if question.answer_variant_id and question.answer_variant_id not in known_variants:
                    issues.append(f"{where}: звук ответа пропал из фонотеки")
                if question.image_id and known_images is not None and question.image_id not in known_images:
                    issues.append(f"{where}: картинка пропала из фонотеки")

        return issues

    def used_variant_ids(self) -> set[str]:
        """Все заготовки, которые реально нужны для экспорта."""
        used: set[str] = set()
        for theme in self.themes:
            for question in theme.questions:
                used.add(question.variant_id)
                if question.answer_variant_id:
                    used.add(question.answer_variant_id)
        return {identifier for identifier in used if identifier}
