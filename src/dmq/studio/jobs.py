"""Фоновые задачи.

Разбор трека занимает секунды, а упаковка игры — десятки секунд. Держать на
этом открытый HTTP-запрос нельзя: браузер отвалится по таймауту, а человек
решит, что всё зависло. Поэтому долгие операции уходят в поток, а страница
опрашивает их состояние и показывает прогресс.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Job:
    id: str
    title: str
    total: int = 1
    done: int = 0
    status: str = "running"   # running | finished | failed
    message: str = ""
    result: Any = None
    log: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "total": self.total,
            "done": self.done,
            "status": self.status,
            "message": self.message,
            "result": self.result,
            "log": self.log[-40:],
        }


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def start(self, title: str, total: int, work: Callable[[Job], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:10], title=title, total=max(total, 1))
        with self._lock:
            self._jobs[job.id] = job

        def runner() -> None:
            try:
                job.result = work(job)
                job.status = "finished"
                job.done = job.total
            except Exception as error:  # noqa: BLE001 — любую поломку показываем человеку
                job.status = "failed"
                job.message = str(error) or error.__class__.__name__
                job.log.append(traceback.format_exc(limit=3))

        threading.Thread(target=runner, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)
