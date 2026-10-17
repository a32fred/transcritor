"""Servidor web local (biblioteca padrão + jinja2, sem frameworks externos).

Rotas
-----
GET    /                          interface
GET    /api/status                estado geral (modelo, contadores, pastas)
GET    /api/jobs                  lista de jobs
POST   /api/upload?name=arq.mp3   envia bytes crus do arquivo
POST   /api/jobs                  adiciona caminhos locais (JSON {"paths": [...]})
POST   /api/jobs/{id}/cancel      cancela
POST   /api/jobs/{id}/retry       reenfileira
DELETE /api/jobs/{id}             remove do histórico
POST   /api/jobs/clear            limpa finalizados
GET    /api/transcript/{id}       texto da transcrição (?format=txt|md|srt|vtt|json)
GET    /api/download/{id}/{fmt}   baixa o arquivo
GET    /api/audio/{id}            stream do áudio original (com Range)
GET    /api/models                modelos disponíveis/baixados
GET    /api/events                Server-Sent Events com progresso ao vivo
"""

from __future__ import annotations

import contextlib
import json
import logging
import mimetypes
import os
import queue
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..config import Config
from ..engine import model_status
from ..jobs import JobStore
from ..media import is_media_file
from ..runner import Runner
from ..watcher import Watcher

log = logging.getLogger("transcritor.web")

TEMPLATE_DIR = Path(__file__).parent / "templates"
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


# --------------------------------------------------------------------------
# Barramento de eventos (SSE)
# --------------------------------------------------------------------------

class EventBus:
    def __init__(self) -> None:
        self._subs: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._history: list[dict] = []

    def publish(self, event: str, data: dict) -> None:
        payload = {"event": event, "data": data, "ts": time.time()}
        with self._lock:
            self._history.append(payload)
            del self._history[:-200]
            subs = list(self._subs)
        for q in subs:
            with contextlib.suppress(queue.Full):
                q.put_nowait(payload)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self._lock:
            self._subs.add(q)
            for item in self._history[-40:]:
                try:
                    q.put_nowait(item)
                except queue.Full:
                    break
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subs.discard(q)


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "transcritor"
    protocol_version = "HTTP/1.1"

    # injetados pelo servidor
    cfg: Config
    store: JobStore
    runner: Runner
    watcher: Watcher | None
    bus: EventBus

    # ---- infraestrutura -------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:  # silencia o log ruidoso
        log.debug("%s - %s", self.address_string(), fmt % args)

    def log_error(self, fmt: str, *args: Any) -> None:
        """Erros de protocolo (requisição malformada, cliente que sumiu) vão
        para o log de debug, não para o stderr do usuário."""
        log.debug("%s - %s", self.address_string(), fmt % args)

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # o cliente fechou a conexão no meio — normal em navegadores
            self.close_connection = True

    def _json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _text(self, text: str, status: int = 200, ctype: str = "text/plain; charset=utf-8",
              download: str | None = None) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{download}"')
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, msg: str) -> None:
        self._json({"error": msg}, status)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        remaining = length
        chunks: list[bytes] = []
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _body_json(self) -> dict:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ---- GET ------------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)

        try:
            if path == "/":
                return self._index()
            if path == "/api/status":
                return self._api_status()
            if path == "/api/jobs":
                return self._json([j.to_dict() for j in self.store.all(limit=300)])
            if path == "/api/models":
                return self._json({"active": self.cfg.model, "models": model_status(self.cfg)})
            if path == "/api/events":
                return self._api_events()
            if path.startswith("/api/transcript/"):
                return self._api_transcript(path.rsplit("/", 1)[-1], qs)
            if path.startswith("/api/download/"):
                return self._api_download(path)
            if path.startswith("/api/audio/"):
                return self._api_audio(path.rsplit("/", 1)[-1])
            if path == "/favicon.ico":
                return self._text("", 204)
            return self._error(404, "rota não encontrada")
        except BrokenPipeError:
            return
        except Exception as exc:
            log.exception("erro em GET %s", self.path)
            return self._error(500, str(exc))

    # ---- POST -----------------------------------------------------------
    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)

        try:
            if path == "/api/upload":
                return self._api_upload(qs)
            if path == "/api/jobs":
                return self._api_add_paths()
            if path == "/api/jobs/clear":
                return self._api_clear()
            m = re.fullmatch(r"/api/jobs/([A-Za-z0-9_-]+)/(cancel|retry)", path)
            if m:
                job_id, action = m.groups()
                return self._api_job_action(job_id, action)
            if path == "/api/scan":
                n = self.watcher.scan_once() if self.watcher else 0
                return self._json({"enqueued": n})
            return self._error(404, "rota não encontrada")
        except BrokenPipeError:
            return
        except Exception as exc:
            log.exception("erro em POST %s", self.path)
            return self._error(500, str(exc))

    def do_DELETE(self) -> None:
        path = urllib.parse.urlparse(self.path).path.rstrip("/")
        m = re.fullmatch(r"/api/jobs/([A-Za-z0-9_-]+)", path)
        if m:
            ok = self.store.remove(m.group(1))
            return self._json({"removed": ok})
        return self._error(404, "rota não encontrada")

    # ---- rotas ----------------------------------------------------------
    def _index(self) -> None:
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=select_autoescape(["html"]),
        )
        tpl = env.get_template("index.html")
        html = tpl.render(
            model=self.cfg.model,
            device=f"{self.cfg.device}/{self.cfg.compute_type}",
            threads=self.cfg.cpu_threads,
            inbox=str(self.cfg.inbox),
            outbox=str(self.cfg.outbox),
            language=self.cfg.language,
            formats=", ".join(self.cfg.formats),
            version=__import__("transcritor").__version__,
        )
        self._text(html, 200, "text/html; charset=utf-8")

    def _api_status(self) -> None:
        counts = self.store.counts()
        self._json({
            "counts": counts,
            "model": self.cfg.model,
            "device": self.cfg.device,
            "compute_type": self.cfg.compute_type,
            "threads": self.cfg.cpu_threads,
            "language": self.cfg.language,
            "engine_loaded": self.runner.engine.loaded,
            "watching": bool(self.watcher and self.watcher.running),
            "inbox": str(self.cfg.inbox),
            "outbox": str(self.cfg.outbox),
            "model_ready": bool(self.cfg.local_model_path),
            "uptime": round(time.time() - self.server.started_at, 1),  # type: ignore[attr-defined]
        })

    def _api_transcript(self, job_id: str, qs: dict) -> None:
        job = self.store.get(job_id)
        if not job:
            return self._error(404, "job não encontrado")
        fmt = (qs.get("format") or ["txt"])[0]
        if job.status != "done":
            return self._json({
                "status": job.status,
                "error": job.error,
                "progress": job.progress,
                "preview": job.preview,
            })
        path = (job.outputs or {}).get(fmt)
        if not path or not Path(path).exists():
            return self._error(404, f"formato {fmt} não disponível para este job")
        return self._text(Path(path).read_text(encoding="utf-8"))

    def _api_download(self, path: str) -> None:
        parts = path.split("/")
        if len(parts) < 5:
            return self._error(400, "caminho inválido")
        job_id, fmt = parts[3], parts[4]
        job = self.store.get(job_id)
        if not job:
            return self._error(404, "job não encontrado")
        file_path = (job.outputs or {}).get(fmt)
        if not file_path or not Path(file_path).exists():
            return self._error(404, "arquivo não disponível")
        p = Path(file_path)
        data = p.read_bytes()
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition",
                         f'attachment; filename="{job.stem}.{fmt}"')
        self.end_headers()
        self.wfile.write(data)

    def _api_audio(self, job_id: str) -> None:
        job = self.store.get(job_id)
        if not job:
            return self._error(404, "job não encontrado")
        p = Path(job.source)
        if not p.exists():
            return self._error(404, "arquivo original não encontrado")

        size = p.stat().st_size
        ctype = mimetypes.guess_type(p.name)[0] or "audio/mpeg"
        start, end = 0, size - 1

        rng = self.headers.get("Range")
        status = 200
        if rng:
            m = _RANGE_RE.fullmatch(rng.strip())
            if m:
                s, e = m.groups()
                if s:
                    start = int(s)
                if e:
                    end = min(int(e), size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()

        with p.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)

    def _api_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q = self.bus.subscribe()
        try:
            self.wfile.write(b": conectado\n\n")
            self.wfile.flush()
            last_ping = time.time()
            while True:
                try:
                    item = q.get(timeout=1.0)
                except queue.Empty:
                    if time.time() - last_ping > 15:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        last_ping = time.time()
                    continue
                payload = json.dumps(item["data"], ensure_ascii=False, default=str)
                msg = f"event: {item['event']}\ndata: {payload}\n\n"
                self.wfile.write(msg.encode("utf-8"))
                self.wfile.flush()
                last_ping = time.time()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.bus.unsubscribe(q)

    # ---- upload / fila --------------------------------------------------
    def _api_upload(self, qs: dict) -> None:
        raw_name = (qs.get("name") or [""])[0] or self.headers.get("X-Filename") or ""
        name = Path(urllib.parse.unquote(raw_name)).name
        if not name:
            return self._error(400, "nome do arquivo ausente")

        suffix = Path(name).suffix.lower()
        if not is_media_file(name):
            return self._error(400, f"extensão não suportada: {suffix or '(sem extensão)'}")

        self.cfg.inbox.mkdir(parents=True, exist_ok=True)
        target = self.cfg.inbox / name
        n = 1
        while target.exists():
            target = self.cfg.inbox / f"{Path(name).stem}-{n}{suffix}"
            n += 1

        data = self._read_body()
        if not data:
            return self._error(400, "corpo vazio")

        tmp = target.with_suffix(target.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, target)

        job = self.store.add(target, origin="web")
        self.runner.submit(job.id)
        self.bus.publish("enqueued", {"job_id": job.id, "filename": target.name})
        return self._json({
            "job_id": job.id,
            "filename": target.name,
            "bytes": len(data),
            "path": str(target),
        }, 201)

    def _api_add_paths(self) -> None:
        body = self._body_json()
        paths = body.get("paths") or []
        if isinstance(paths, str):
            paths = [paths]
        added = []
        for raw in paths:
            p = Path(str(raw)).expanduser()
            if not p.exists() or not p.is_file():
                continue
            if not is_media_file(p):
                continue
            job = self.store.add(p, origin="web")
            self.runner.submit(job.id)
            added.append({"job_id": job.id, "filename": p.name})
        return self._json({"added": added, "count": len(added)})

    def _api_job_action(self, job_id: str, action: str) -> None:
        job = self.store.get(job_id)
        if not job:
            return self._error(404, "job não encontrado")
        if action == "cancel":
            ok = self.runner.cancel(job_id)
            self.bus.publish("canceled", {"job_id": job_id})
            return self._json({"canceled": bool(ok)})
        # retry
        self.store.update(job_id, status="queued", error="", progress={"phase": "na fila"},
                          started=None, finished=None)
        self.runner.submit(job_id)
        self.bus.publish("enqueued", {"job_id": job_id, "filename": job.filename})
        return self._json({"requeued": True})

    def _api_clear(self) -> None:
        n = self.store.clear_finished()
        return self._json({"removed": n})


# --------------------------------------------------------------------------
# Servidor
# --------------------------------------------------------------------------

class TranscritorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], handler, cfg: Config,
                 *, watch: bool = True) -> None:
        super().__init__(addr, handler)
        self.cfg = cfg
        self.bus = EventBus()
        self.store = JobStore(cfg)
        self.runner = Runner(cfg, self.store)
        self.runner.on_event = lambda e, d: self.bus.publish(e, d)
        self.watcher = Watcher(cfg, self.store, self.runner,
                               on_event=lambda e, d: self.bus.publish(e, d)) if watch else None
        self.started_at = time.time()

    def handle_error(self, request, client_address) -> None:
        """O cliente desconectar no meio do download não é um erro do servidor."""
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        log.exception("erro ao atender %s", client_address)


def serve(cfg: Config, *, host: str | None = None, port: int | None = None,
          open_browser: bool = False, watch: bool = True) -> int:
    cfg.ensure_dirs()
    host = host or cfg.web_host
    port = port or cfg.web_port

    server = TranscritorServer((host, port), Handler, cfg, watch=watch)

    Handler.cfg = cfg
    Handler.store = server.store
    Handler.runner = server.runner
    Handler.watcher = server.watcher
    Handler.bus = server.bus

    server.runner.start()
    n = server.runner.submit_pending()
    if watch:
        server.watcher.start()

    url = f"http://{host}:{port}/"
    log.info("servidor em %s (modelo %s, %d pendente(s) na fila)", url, cfg.model, n)
    print(f"\n  transcritor no ar: {url}")
    print(f"  entrada: {cfg.inbox}")
    print(f"  saída  : {cfg.outbox}")
    print(f"  modelo : {cfg.model} ({cfg.device}/{cfg.compute_type}, {cfg.cpu_threads} threads)")
    print("  Ctrl+C para encerrar\n")

    if open_browser:
        threading.Timer(1.0, lambda: __import__("webbrowser").open(url)).start()

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n  encerrando…")
    finally:
        if watch:
            server.watcher.stop()
        server.runner.stop()
        server.store.save(force=True)
        server.shutdown()
        server.server_close()
    return 0
