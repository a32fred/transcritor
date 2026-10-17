"""Fila de jobs persistente.

Um "job" é um arquivo de mídia que entra na fila para ser transcrito.
O estado vive em um único JSON (``cfg.state_file``) protegido por lock —
simples, inspecionável e suficiente para uso em máquina única.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from .config import Config

Status = Literal["queued", "running", "done", "error", "canceled"]

_ACTIVE: tuple[Status, ...] = ("queued", "running")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Job:
    id: str
    source: str
    filename: str
    status: Status = "queued"
    origin: str = "cli"                 # cli | watch | web | tui
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    duration: float = 0.0
    progress: dict[str, Any] = field(default_factory=dict)
    language: str = ""
    language_probability: float = 0.0
    model: str = ""
    elapsed: float = 0.0
    chars: int = 0
    words: int = 0
    preview: str = ""
    outputs: dict[str, str] = field(default_factory=dict)
    error: str = ""
    outdir: str = ""

    # ------------------------------------------------------------------
    @property
    def stem(self) -> str:
        return Path(self.filename).stem

    @property
    def age(self) -> float:
        return (self.finished or time.time()) - self.created

    @property
    def is_active(self) -> bool:
        return self.status in _ACTIVE

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["is_active"] = self.is_active
        if self.started:
            d["run_seconds"] = round((self.finished or time.time()) - self.started, 2)
        else:
            d["run_seconds"] = 0.0
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Job:
        valid = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in valid})


class JobStore:
    """Store em JSON, thread-safe, com escrita atômica."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.path = Path(cfg.state_file)
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._dirty = False
        self.load()

    # ---- persistência --------------------------------------------------
    def load(self) -> None:
        with self._lock:
            self._jobs.clear()
            self._order.clear()
            if not self.path.exists():
                return
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return
            for raw in data.get("jobs", []):
                try:
                    job = Job.from_dict(raw)
                except TypeError:
                    continue
                # jobs interrompidos por queda ficam marcados
                if job.status == "running":
                    job.status = "error"
                    job.error = job.error or "interrompido (processo encerrado durante a transcrição)"
                self._jobs[job.id] = job
                self._order.append(job.id)

    def save(self, force: bool = False) -> None:
        with self._lock:
            if not self._dirty and not force:
                return
            payload = {
                "version": 1,
                "saved_at": time.time(),
                "jobs": [self._jobs[i].to_dict() for i in self._order if i in self._jobs],
            }
            tmp = self.path.with_suffix(".json.tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            self._dirty = False

    # ---- consultas -----------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def find_by_source(self, source: str) -> Job | None:
        src = str(Path(source).resolve())
        with self._lock:
            for jid in reversed(self._order):
                j = self._jobs.get(jid)
                if j and str(Path(j.source).resolve()) == src:
                    return j
        return None

    def all(self, *, limit: int | None = None, status: str | None = None,
            search: str = "") -> list[Job]:
        with self._lock:
            jobs = [self._jobs[i] for i in self._order if i in self._jobs]
        if status:
            jobs = [j for j in jobs if j.status == status]
        if search:
            needle = search.lower()
            jobs = [j for j in jobs if needle in j.filename.lower() or needle in j.id.lower()]
        jobs.sort(key=lambda j: j.created, reverse=True)
        return jobs[:limit] if limit else jobs

    def counts(self) -> dict[str, int]:
        with self._lock:
            out = dict.fromkeys(("queued", "running", "done", "error", "canceled"), 0)
            for j in self._jobs.values():
                out[j.status] = out.get(j.status, 0) + 1
            out["total"] = len(self._jobs)
        return out

    def next_queued(self) -> Job | None:
        with self._lock:
            for i in self._order:
                j = self._jobs.get(i)
                if j and j.status == "queued":
                    return j
        return None

    # ---- mutações ------------------------------------------------------
    def add(self, source: str | Path, *, origin: str = "cli",
            job_id: str | None = None, force: bool = False) -> Job:
        src = Path(source).expanduser()
        with self._lock:
            if not force:
                existing = self.find_by_source(str(src))
                if existing and existing.status in ("queued", "running", "done"):
                    return existing
            job = Job(
                id=job_id or new_id(),
                source=str(src),
                filename=src.name,
                origin=origin,
                status="queued",
            )
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._dirty = True
        self.save()
        return job

    def update(self, job_id: str, **fields: Any) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            for k, v in fields.items():
                if hasattr(job, k):
                    setattr(job, k, v)
            self._dirty = True
        self.save()
        return job

    def set_progress(self, job_id: str, progress: dict[str, Any], *, persist: bool = False) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.progress = progress
            self._dirty = True
        if persist:
            self.save()

    def remove(self, job_id: str) -> bool:
        with self._lock:
            if job_id not in self._jobs:
                return False
            del self._jobs[job_id]
            self._order = [i for i in self._order if i != job_id]
            self._dirty = True
        self.save()
        return True

    def clear_finished(self) -> int:
        with self._lock:
            keep = [i for i in self._order if self._jobs[i].status in _ACTIVE]
            removed = len(self._order) - len(keep)
            for i in list(self._jobs):
                if i not in keep:
                    del self._jobs[i]
            self._order = keep
            self._dirty = True
        self.save()
        return removed

    def requeue_failed(self) -> int:
        """Devolve à fila os jobs que terminaram com erro.

        Jobs interrompidos por queda do processo são marcados como 'error' no
        carregamento; este método permite refazê-los em lote.
        """
        n = 0
        with self._lock:
            for j in self._jobs.values():
                if j.status == "error":
                    j.status = "queued"
                    j.error = ""
                    j.started = None
                    j.finished = None
                    j.progress = {"phase": "na fila"}
                    n += 1
            self._dirty = bool(n)
        if n:
            self.save()
        return n
