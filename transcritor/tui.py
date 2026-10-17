"""Interface de terminal (curses) — painel ao vivo da fila de transcrição.

Teclas:
  q / Q        sair
  r            reescanear a inbox agora
  s            processar tudo que está 'queued'
  c            cancelar o job selecionado
  x            remover jobs finalizados
  o            abrir a pasta de saída do job selecionado
  v            ver a transcrição do job selecionado (rolável)
  a            alternar visão: todos / ativos / concluídos / erros
  TAB / S-TAB  mover a seleção
  setas        mover a seleção
  ?            ajuda
"""

from __future__ import annotations

import curses
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import Config
from .formats import ts_human
from .jobs import JobStore
from .runner import Runner
from .watcher import Watcher

log = logging.getLogger("transcritor.tui")

STATUS_LABEL = {
    "queued": "na fila",
    "running": "processando",
    "done": "concluído",
    "error": "erro",
    "canceled": "cancelado",
}

VIEWS = ["todos", "ativos", "concluídos", "erros"]


def _adjacent_files_present() -> bool:
    return bool(shutil.which("ffmpeg"))


class Tui:
    def __init__(self, cfg: Config, *, watch: bool = True) -> None:
        self.cfg = cfg
        cfg.ensure_dirs()
        self.store = JobStore(cfg)
        self.runner = Runner(cfg, self.store)
        self.watcher = Watcher(cfg, self.store, self.runner) if watch else None
        self.view = 0
        self.selected = 0
        self.scroll = 0
        self.toast = ""
        self.toast_until = 0.0
        self.viewer: tuple[str, int] | None = None
        self.help_open = False

    # ------------------------------------------------------------------
    def notify(self, msg: str, seconds: float = 3.0) -> None:
        self.toast = msg
        self.toast_until = time.time() + seconds

    def visible_jobs(self):
        jobs = self.store.all(limit=300)
        if self.view == 1:
            jobs = [j for j in jobs if j.is_active]
        elif self.view == 2:
            jobs = [j for j in jobs if j.status == "done"]
        elif self.view == 3:
            jobs = [j for j in jobs if j.status in ("error", "canceled")]
        return jobs

    # ------------------------------------------------------------------
    def run(self) -> None:
        if not _adjacent_files_present():
            print("aviso: ffmpeg não encontrado no PATH", file=sys.stderr)
        self.runner.start()
        self.runner.submit_pending()
        if self.watcher:
            self.watcher.start()
        try:
            curses.wrapper(self._main)
        finally:
            if self.watcher:
                self.watcher.stop()
            self.runner.stop()
            self.store.save(force=True)

    # ------------------------------------------------------------------
    def _main(self, scr) -> None:
        curses.curs_set(0)
        scr.nodelay(True)
        scr.timeout(250)
        try:
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_GREEN, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_RED, -1)
            curses.init_pair(5, curses.COLOR_MAGENTA, -1)
            curses.init_pair(6, curses.COLOR_WHITE, -1)
            curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_CYAN)
        except curses.error:
            pass

        while True:
            self._draw(scr)
            ch = scr.getch()
            if not self._handle(scr, ch):
                break

    # ------------------------------------------------------------------
    def _handle(self, scr, ch: int) -> bool:
        jobs = self.visible_jobs()

        if ch == -1:
            return True

        if self.viewer is not None:
            text, off = self.viewer
            if ch in (ord("q"), 27):
                self.viewer = None
            elif ch in (curses.KEY_UP, ord("k")):
                self.viewer = (text, max(0, off - 1))
            elif ch in (curses.KEY_DOWN, ord("j")):
                self.viewer = (text, off + 1)
            elif ch == curses.KEY_PPAGE:
                self.viewer = (text, max(0, off - 20))
            elif ch == curses.KEY_NPAGE:
                self.viewer = (text, off + 20)
            elif ch == curses.KEY_HOME:
                self.viewer = (text, 0)
            return True

        if self.help_open:
            if ch in (ord("?"), ord("q"), 27):
                self.help_open = False
            return True

        if ch in (ord("q"), ord("Q")):
            return False
        elif ch == ord("r"):
            if self.watcher:
                n = self.watcher.scan_once()
                self.notify(f"varredura: {n} novo(s) job(s)")
            else:
                self.notify("vigilância desativada")
        elif ch == ord("s"):
            n = self.runner.submit_pending()
            self.notify(f"{n} job(s) enfileirado(s)" if n else "nada na fila")
        elif ch == ord("x"):
            n = self.store.clear_finished()
            self.notify(f"{n} job(s) removido(s)")
        elif ch == ord("a"):
            self.view = (self.view + 1) % len(VIEWS)
            self.selected = 0
            self.scroll = 0
            self.notify(f"visão: {VIEWS[self.view]}")
        elif ch == ord("?"):
            self.help_open = True
        elif ch in (curses.KEY_UP, ord("k")):
            self.selected = max(0, self.selected - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            self.selected = min(max(0, len(jobs) - 1), self.selected + 1)
        elif ch == 9:  # TAB
            self.selected = (self.selected + 1) % max(1, len(jobs))
        elif ch == curses.KEY_BTAB:
            self.selected = (self.selected - 1) % max(1, len(jobs))
        elif ch in (curses.KEY_HOME,):
            self.selected = 0
        elif ch in (curses.KEY_END,):
            self.selected = max(0, len(jobs) - 1)
        elif ch == ord("c") and jobs:
            j = jobs[min(self.selected, len(jobs) - 1)]
            ok = self.runner.cancel(j.id)
            self.notify("cancelando…" if ok else "não foi possível cancelar")
        elif ch == ord("o") and jobs:
            j = jobs[min(self.selected, len(jobs) - 1)]
            path = j.outdir or str(self.cfg.outbox)
            self._open_path(path)
        elif ch == ord("v") and jobs:
            j = jobs[min(self.selected, len(jobs) - 1)]
            self.viewer = (self._job_text(j), 0)
        return True

    def _open_path(self, path: str) -> None:
        p = Path(path)
        if not p.exists():
            self.notify("pasta ainda não existe")
            return
        opener = None
        for cand in ("xdg-open", "open"):
            if shutil.which(cand):
                opener = cand
                break
        if not opener:
            self.notify(f"abra manualmente: {p}")
            return
        try:
            subprocess.Popen([opener, str(p)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            self.notify(f"abrindo {p}")
        except OSError as exc:
            self.notify(f"falha ao abrir: {exc}")

    def _job_text(self, job) -> str:
        for fmt in ("txt", "md", "srt"):
            p = job.outputs.get(fmt)
            if p and Path(p).exists():
                try:
                    return Path(p).read_text(encoding="utf-8")
                except OSError:
                    continue
        if job.preview:
            return job.preview + "\n\n(prévia — arquivo final ainda não disponível)"
        return "sem transcrição disponível"

    # ------------------------------------------------------------------
    def _draw(self, scr) -> None:
        scr.erase()
        h, w = scr.getmaxyx()
        if h < 12 or w < 60:
            self._safe_add(scr, 0, 0, "terminal muito pequeno (mínimo 60x12)", curses.A_BOLD)
            scr.refresh()
            return

        if self.viewer is not None:
            self._draw_viewer(scr, h, w)
            scr.refresh()
            return

        jobs = self.visible_jobs()
        counts = self.store.counts()

        # ---- cabeçalho -------------------------------------------------
        title = " TRANSCRITOR "
        self._safe_add(scr, 0, 0, title, curses.A_REVERSE | curses.A_BOLD)
        right = f" {VIEWS[self.view]} · {len(jobs)} exibidos "
        mid = (f" modelo {self.cfg.model} · {self.cfg.device}/{self.cfg.compute_type}"
               f" · {self.cfg.cpu_threads} threads ")
        room = max(0, w - len(title) - len(right))
        self._safe_add(scr, 0, len(title), mid[:room], curses.A_REVERSE)
        self._safe_add(scr, 0, max(0, w - len(right)), right, curses.A_REVERSE | curses.A_BOLD)

        # ---- barra de status -------------------------------------------
        watch_state = "ON" if (self.watcher and self.watcher.running) else "OFF"
        engine = "carregado" if self.runner.engine.loaded else "carregando…"
        line = (
            f" fila {counts['queued']}  processando {counts['running']}  "
            f"concluídos {counts['done']}  erros {counts['error']}  "
            f"| vigília {watch_state}  | modelo {engine} "
        )
        self._safe_add(scr, 1, 0, line[:w - 1], curses.color_pair(1))
        self._safe_add(scr, 2, 0, "─" * (w - 1), curses.color_pair(1))

        # ---- colunas ---------------------------------------------------
        hdr = (
            f"{'N':>3}  {'ARQUIVO':<34} {'STATUS':<12} {'DURAÇÃO':>11} "
            f"{'ANDOU':>7} {'%':>4} {'IDIOMA':>6} {'PALAVRAS':>9}"
        )
        self._safe_add(scr, 3, 0, hdr[:w - 1], curses.A_BOLD | curses.color_pair(6))

        list_top = 4
        list_bottom = h - 4
        avail = max(1, list_bottom - list_top)

        if self.selected < self.scroll:
            self.scroll = self.selected
        elif self.selected >= self.scroll + avail:
            self.scroll = self.selected - avail + 1

        if not jobs:
            self._safe_add(scr, list_top, 2, "nada por aqui — jogue um áudio em "
                                            f"{self.cfg.inbox} e aperte r", curses.color_pair(3))
        else:
            for i in range(self.scroll, min(len(jobs), self.scroll + avail)):
                j = jobs[i]
                self._draw_row(scr, list_top + (i - self.scroll), w, j, i + 1, i == self.selected)

        # ---- rodapé ----------------------------------------------------
        self._safe_add(scr, h - 3, 0, "─" * (w - 1), curses.color_pair(1))
        if self.toast and time.time() < self.toast_until:
            self._safe_add(scr, h - 2, 0, f" {self.toast}"[:w - 1], curses.color_pair(3))
        else:
            keyhint = (" r recarregar  s processar  c cancelar  v ver  o abrir  "
                       "a filtrar  x limpar  ? ajuda  q sair ")
            self._safe_add(scr, h - 2, 0, keyhint[:w - 1], curses.color_pair(1))

        sel = jobs[min(self.selected, len(jobs) - 1)] if jobs else None
        if sel:
            detail = f" {sel.filename}  ·  origem {sel.origin}  ·  id {sel.id} "
            if sel.outdir:
                detail += f" ·  {sel.outdir} "
            self._safe_add(scr, h - 1, 0, detail[:w - 1], curses.A_DIM)

        if self.help_open:
            self._draw_help(scr, h, w)

    def _draw_row(self, scr, row: int, w: int, job, num: int, active: bool) -> None:
        attr = curses.color_pair(7) if active else curses.A_NORMAL
        st = STATUS_LABEL.get(job.status, job.status)
        dur = ts_human(job.duration) if job.duration else "—"
        prog = job.progress or {}
        pct = prog.get("percent")
        pct_s = f"{pct:.0f}" if isinstance(pct, (int, float)) else ("100" if job.status == "done" else "—")
        run = f"{job.to_dict()['run_seconds']:.0f}s" if job.started else "—"
        words = str(job.words) if job.words else "—"
        name = job.filename if len(job.filename) <= 34 else job.filename[:31] + "…"
        line = (f"{num:>3}  {name:<34} {st:<12} {dur:>11} {run:>7} {pct_s:>4} "
                f"{(job.language or '—'):>6} {words:>9}")

        # Indicador à direita (barra de progresso, erro ou espera). A linha de
        # colunas é encurtada para abrir espaço: antes os dois se sobrepunham.
        extra = ""
        extra_attr = curses.A_NORMAL
        if job.status == "running" and isinstance(pct, (int, float)):
            bw = max(10, min(40, w - 62))
            filled = int(bw * min(100.0, max(0.0, pct)) / 100.0)
            extra = "█" * filled + "░" * (bw - filled)
            extra_attr = curses.color_pair(2) if not active else attr
        elif job.status == "error" and job.error:
            extra = job.error[:max(5, w - 72)]
            extra_attr = curses.color_pair(4) if not active else attr
        elif job.status == "queued":
            extra = "aguardando"
            extra_attr = curses.color_pair(3) if not active else attr

        if extra:
            start = max(0, w - len(extra) - 2)
            self._safe_add(scr, row, 0, line[:max(0, start - 1)], attr)
            self._safe_add(scr, row, start, extra, extra_attr)
        else:
            self._safe_add(scr, row, 0, line[:w - 1], attr)

        # linha inferior com prévia
        preview = (job.preview or "").replace("\n", " ")
        if preview:
            pass  # a prévia aparece no visualizador (tecla v)

    def _draw_viewer(self, scr, h: int, w: int) -> None:
        text, off = self.viewer  # type: ignore[misc]
        lines: list[str] = []
        for raw in text.splitlines():
            while len(raw) > w - 2:
                lines.append(raw[:w - 2])
                raw = raw[w - 2:]
            lines.append(raw)
        title = f" TRANSCRIÇÃO — linhas {off + 1}-{min(len(lines), off + h - 2)} de {len(lines)} "
        self._safe_add(scr, 0, 0, title[:w - 1], curses.A_REVERSE | curses.A_BOLD)
        for i in range(h - 2):
            idx = off + i
            if idx >= len(lines):
                break
            self._safe_add(scr, 1 + i, 0, lines[idx][:w - 1])
        self._safe_add(scr, h - 1, 0, " ↑↓ rolar  PgUp/PgDn página  Home topo  q voltar "[:w - 1],
                       curses.color_pair(1))

    def _draw_help(self, scr, h: int, w: int) -> None:
        lines = [
            "AJUDA — transcritor",
            "",
            "  r          reescanear a pasta de entrada agora",
            "  s          processar todos os jobs que estão na fila",
            "  c          cancelar o job selecionado",
            "  x          remover jobs já finalizados (limpa a lista)",
            "  v          ver a transcrição do job selecionado",
            "  o          abrir a pasta de saída do job selecionado",
            "  a          alternar filtro: todos / ativos / concluídos / erros",
            "  TAB, ↑ ↓   mover a seleção",
            "  ?          abre/fecha esta ajuda",
            "  q          sair (a transcrição em andamento continua? não — encerra)",
            "",
            f"  Entrada : {self.cfg.inbox}",
            f"  Saída   : {self.cfg.outbox}",
            f"  Modelo  : {self.cfg.model} ({self.cfg.device}/{self.cfg.compute_type})",
            "",
            "  Enter ou q fecham esta ajuda.",
        ]
        box_h = min(len(lines) + 2, h - 2)
        box_w = min(max(len(x) for x in lines) + 4, w - 2)
        top = max(1, (h - box_h) // 2)
        left = max(1, (w - box_w) // 2)
        # apaga o conteúdo de fundo para nada vazar sob a caixa
        for y in range(h):
            self._safe_add(scr, y, 0, " " * (w - 1))
        for i in range(box_h):
            self._safe_add(scr, top + i, left, " " * (box_w - 1), curses.A_REVERSE)
        for i, line in enumerate(lines[:box_h - 2]):
            self._safe_add(scr, top + 1 + i, left + 2, line, curses.A_REVERSE)

    @staticmethod
    def _safe_add(scr, y: int, x: int, text: str, attr: int = 0) -> None:
        try:
            h, w = scr.getmaxyx()
            if y < 0 or y >= h or x < 0 or x >= w:
                return
            room = w - x
            # a última célula da última linha não pode ser escrita no curses
            if y == h - 1:
                room -= 1
            if room <= 0:
                return
            scr.addstr(y, x, text[:room], attr)
        except curses.error:
            pass


def run_tui(cfg: Config, *, watch: bool = True) -> int:
    Tui(cfg, watch=watch).run()
    return 0
