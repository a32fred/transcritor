"""Pasta vigiada: tudo que aparece em var/inbox é transcrito automaticamente.

Implementação por polling (sem dependências externas). Um arquivo só é
considerado pronto quando o tamanho e o mtime ficam estáveis por
``cfg.watch_settle_seconds`` — assim cópias grandes em andamento não são
pegas no meio.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .jobs import JobStore
from .runner import Runner

log = logging.getLogger("transcritor.watcher")

IGNORED_SUFFIXES = (".part", ".tmp", ".crdownload", ".download", ".filepart", ".partial")
IGNORED_PREFIXES = (".", "~$")


@dataclass
class FileState:
    size: int
    mtime: float
    seen: float = field(default_factory=time.time)


class Watcher:
    def __init__(self, cfg: Config, store: JobStore, runner: Runner,
                 *, on_event: Callable[[str, dict], None] | None = None) -> None:
        self.cfg = cfg
        self.store = store
        self.runner = runner
        self.on_event = on_event
        self._seen: dict[str, FileState] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = {"scanned": 0, "enqueued": 0, "skipped": 0, "errors": 0}

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="watcher", daemon=True)
        self._thread.start()
        log.info("vigilância ativa em %s (intervalo %.1fs)", self.cfg.inbox, self.cfg.watch_interval)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        log.info("vigilância parada")

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------
    def scan_once(self, *, submit: bool = True) -> int:
        """Uma passada pela inbox. Retorna quantos jobs novos foram criados.

        Com ``submit=False`` os jobs são criados mas não enviados à fila do
        runner — usado pelo modo ``--once``, que processa em série.
        """
        inbox = self.cfg.inbox
        inbox.mkdir(parents=True, exist_ok=True)
        exts = self.cfg.extensions()
        settle = self.cfg.watch_settle_seconds
        now = time.time()
        enqueued = 0

        candidates: list[Path] = []
        try:
            entries = list(inbox.rglob("*"))
        except OSError as exc:
            log.warning("não foi possível ler a inbox: %s", exc)
            self.stats["errors"] += 1
            return 0

        for p in entries:
            self.stats["scanned"] += 1
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            name = p.name
            if name.startswith(IGNORED_PREFIXES) or p.suffix.lower() in IGNORED_SUFFIXES:
                continue
            if p.suffix.lower() not in exts:
                continue
            candidates.append(p)

        for p in candidates:
            key = str(p)
            try:
                st = p.stat()
            except OSError:
                continue

            # já existe job para esse arquivo?
            if self.store.find_by_source(p):
                self.stats["skipped"] += 1
                self._seen.pop(key, None)
                continue

            prev = self._seen.get(key)
            if prev and prev.size == st.st_size and abs(prev.mtime - st.st_mtime) < 1e-6:
                if now - prev.seen >= settle:
                    self._seen.pop(key, None)
                    job = self.store.add(p, origin="watch")
                    if submit:
                        self.runner.submit(job.id)
                    enqueued += 1
                    self.stats["enqueued"] += 1
                    log.info("enfileirado: %s", p.name)
                    if self.on_event:
                        self.on_event("enqueued", {"job_id": job.id, "filename": p.name})
                # senão: ainda aguardando estabilidade
            else:
                self._seen[key] = FileState(size=st.st_size, mtime=st.st_mtime, seen=now)

        # esquece arquivos que desapareceram
        for key in list(self._seen):
            if not Path(key).exists():
                self._seen.pop(key, None)

        return enqueued

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception:
                log.exception("erro no ciclo de vigilância")
                self.stats["errors"] += 1
            self._stop.wait(self.cfg.watch_interval)
