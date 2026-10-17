"""Runner: pool de workers que consome a fila de jobs.

Usado por `watch`, `serve` e `tui`. Garante que apenas um modelo fique
carregado na memória e que o uso de CPU seja controlado (``watch_workers``).
"""

from __future__ import annotations

import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Config
from .engine import Engine
from .jobs import Job, JobStore
from .pipeline import Pipeline

log = logging.getLogger("transcritor.runner")


class Runner:
    """Consome jobs do store em N threads e atualiza o estado conforme anda."""

    def __init__(self, cfg: Config, store: JobStore, *, workers: int | None = None,
                 formats: tuple[str, ...] | None = None,
                 preprocess: bool | None = None,
                 loudnorm: bool = False,
                 engine: Engine | None = None) -> None:
        self.cfg = cfg
        self.store = store
        self.workers = max(1, workers if workers is not None else cfg.watch_workers)
        self.formats = formats
        self.preprocess = preprocess
        self.loudnorm = loudnorm

        # uma Engine só: o modelo é grande, não faz sentido replicar
        self.engine = engine if engine is not None else Engine(cfg)
        self.pipeline = Pipeline(cfg, self.engine)

        self._q: queue.Queue[str | None] = queue.Queue()
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._running = False
        self._cancels: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

        self.on_event: Callable[[str, dict[str, Any]], None] | None = None

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    @property
    def busy(self) -> bool:
        return any(t.is_alive() for t in self._threads) and not self._q.empty()

    def emit(self, event: str, **data: Any) -> None:
        if self.on_event:
            try:
                self.on_event(event, data)
            except Exception:  # noqa: BLE001
                log.debug("on_event falhou", exc_info=True)

    # ------------------------------------------------------------------
    def start(self, *, warmup: bool = True) -> None:
        if self._running:
            return
        self._stop.clear()
        self._running = True

        if warmup:
            def _warm() -> None:
                try:
                    t = self.engine.warmup()
                    self.emit("engine_ready", seconds=round(t, 2), model=self.cfg.model)
                except Exception as exc:  # noqa: BLE001
                    log.error("falha ao carregar o modelo: %s", exc)
                    self.emit("engine_error", error=str(exc))
            threading.Thread(target=_warm, name="warmup", daemon=True).start()

        for i in range(self.workers):
            t = threading.Thread(target=self._loop, name=f"worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("runner iniciado com %d worker(s)", self.workers)

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        for _ in self._threads:
            self._q.put(None)
        for t in self._threads:
            t.join(timeout=timeout)
        self._threads.clear()
        self._running = False
        log.info("runner parado")

    # ------------------------------------------------------------------
    def submit(self, job_id: str) -> None:
        self._q.put(job_id)

    def submit_pending(self) -> int:
        """Enfileira tudo que está 'queued' no store (após restart, por ex.)."""
        n = 0
        for job in reversed(self.store.all(status="queued")):
            self._q.put(job.id)
            n += 1
        return n

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            ev = self._cancels.get(job_id)
        if ev:
            ev.set()
            return True
        job = self.store.get(job_id)
        if job and job.status == "queued":
            self.store.update(job_id, status="canceled", error="cancelado antes de iniciar")
            return True
        return False

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job_id = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if job_id is None:
                return
            try:
                self._run_job(job_id)
            except Exception as exc:
                log.exception("erro inesperado no job %s", job_id)
                self.store.update(job_id, status="error", error=f"erro interno: {exc}",
                                  finished=time.time())
                self.emit("job_error", job_id=job_id, error=str(exc))

    def _run_job(self, job_id: str) -> None:
        job: Job | None = self.store.get(job_id)
        if job is None:
            return
        if job.status not in ("queued", "running"):
            return

        cancel = threading.Event()
        with self._lock:
            self._cancels[job_id] = cancel

        source = Path(job.source)
        if not source.exists():
            self.store.update(job_id, status="error",
                              error=f"arquivo não encontrado: {source}",
                              finished=time.time())
            self.emit("job_error", job_id=job_id, error="arquivo não encontrado")
            with self._lock:
                self._cancels.pop(job_id, None)
            return

        self.store.update(job_id, status="running", started=time.time(),
                          model=self.cfg.model, error="", progress={"phase": "iniciando"})
        self.emit("job_start", job_id=job_id, filename=job.filename)

        last_persist = 0.0

        def stage(phase: str, extra: dict[str, Any]) -> None:
            nonlocal last_persist
            payload = {"phase": phase, **extra}
            now = time.time()
            persist = (now - last_persist) > 3.0 or phase in (
                "probing", "normalizing", "transcribing", "writing", "done")
            if persist:
                last_persist = now
            self.store.set_progress(job_id, payload, persist=persist)
            self.emit("job_progress", job_id=job_id, **payload)

        try:
            out = self.pipeline.transcribe_file(
                source,
                job_id=job_id,
                stage=stage,
                cancel=cancel,
                formats=self.formats,
                preprocess=self.preprocess,
                loudnorm=self.loudnorm,
            )
            result = out["result"]
            self.store.update(
                job_id,
                status="done",
                finished=time.time(),
                duration=result.duration,
                elapsed=result.elapsed,
                language=result.language,
                language_probability=result.language_probability,
                model=result.model,
                words=len(result.text.split()),
                chars=len(result.text),
                preview=result.text[:600],
                outputs=out["outputs"],
                outdir=out["outdir"],
                progress={"phase": "done", "percent": 100.0},
            )
            self.emit("job_done", job_id=job_id, outdir=out["outdir"],
                      outputs=out["outputs"], words=len(result.text.split()),
                      elapsed=result.elapsed)

            if self.cfg.watch_delete_source and job.origin == "watch":
                with contextlib.suppress(OSError):
                    source.unlink()
        except Exception as exc:  # noqa: BLE001
            status = "canceled" if cancel.is_set() else "error"
            log.error("job %s falhou: %s", job_id, exc)
            self.store.update(job_id, status=status, finished=time.time(),
                              error=str(exc) if status == "error" else "cancelado")
            self.emit("job_canceled" if status == "canceled" else "job_error",
                      job_id=job_id, error=str(exc))
        finally:
            with self._lock:
                self._cancels.pop(job_id, None)
