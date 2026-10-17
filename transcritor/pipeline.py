"""Pipeline: um arquivo de mídia entra, saídas em vários formatos saem.

Fluxo:
    probe (ffprobe)  ->  normalize opcional (ffmpeg, 16 kHz mono)
                     ->  faster-whisper
                     ->  render txt/srt/vtt/json/md em var/outbox/<nome>/
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import formats as fmt
from .config import Config
from .engine import Engine, TranscriptionResult
from .media import MediaInfo, normalize, probe

log = logging.getLogger("transcritor.pipeline")

StageFn = Callable[[str, dict[str, Any]], None]


class Pipeline:
    """Orquestra transcrição + gravação de artefatos."""

    def __init__(self, cfg: Config, engine: Engine | None = None) -> None:
        self.cfg = cfg
        self.engine = engine or Engine(cfg)
        self._out_lock = threading.Lock()

    # ------------------------------------------------------------------
    def outdir_for(self, source: str | Path, job_id: str | None = None) -> Path:
        """Diretório de saída determinístico e sem colisão para um arquivo.

        Em caso de nomes repetidos usa a extensão como sufixo legível
        (``aula.mp3`` → ``aula-mp3``) e só depois numera.
        """
        stem = Path(source).stem.strip() or "audio"
        safe = "".join(c if (c.isalnum() or c in " ._-()[]") else "_" for c in stem).strip()
        safe = safe or "audio"
        base = self.cfg.outbox / safe
        with self._out_lock:
            if not base.exists():
                return base
            marker = base / ".meta.json"
            own = None
            if marker.exists():
                try:
                    own = json.loads(marker.read_text(encoding="utf-8")).get("source")
                except (json.JSONDecodeError, OSError):
                    own = None
            if own and str(Path(own).resolve()) == str(Path(source).resolve()):
                return base

            ext = Path(source).suffix.lstrip(".").lower()
            disc = ext or ((job_id or "dup")[:6])
            cand = self.cfg.outbox / f"{safe}-{disc}"
            n = 2
            while cand.exists():
                cand = self.cfg.outbox / f"{safe}-{disc}-{n}"
                n += 1
            return cand

    # ------------------------------------------------------------------
    def transcribe_file(
        self,
        source: str | Path,
        *,
        job_id: str | None = None,
        stage: StageFn | None = None,
        cancel: threading.Event | None = None,
        formats: tuple[str, ...] | None = None,
        outdir: Path | None = None,
        preprocess: bool | None = None,
        loudnorm: bool = False,
        out_format: str = "plain",          # texto de saída: plain|srt|vtt|json|all
    ) -> dict[str, Any]:
        source = Path(source).expanduser()
        if not source.exists():
            raise FileNotFoundError(f"arquivo não encontrado: {source}")

        work = self.cfg.work / (job_id or source.stem)
        work.mkdir(parents=True, exist_ok=True)

        def emit(phase: str, **extra: Any) -> None:
            if stage:
                stage(phase, extra)

        # --- 1. ffprobe ---------------------------------------------------
        emit("probing")
        info: MediaInfo = probe(source)
        if not info.has_audio:
            if info.has_video:
                raise ValueError(
                    f"o arquivo tem imagem mas nenhuma trilha de áudio: {source.name}"
                )
            raise ValueError(
                f"não há trilha de áudio reconhecível em {source.name} "
                "(arquivo corrompido, vazio ou em formato não suportado)"
            )
        if info.has_video:
            emit("extract_audio", duration=info.duration)
        emit("probed", **info.to_dict())

        # --- 2. normalização (opcional, recomendada) ----------------------
        audio_in: Path = source
        if preprocess is None:
            preprocess = True
        if preprocess:
            emit("normalizing")
            wav = work / "audio16k.wav"
            try:
                normalize(source, wav, loudnorm=loudnorm)
                audio_in = wav
            except Exception as exc:  # noqa: BLE001
                log.warning("ffmpeg falhou (%s); usando o arquivo original", exc)
                audio_in = source
                emit("normalize_failed", error=str(exc))

        if cancel is not None and cancel.is_set():
            raise RuntimeError("cancelado")

        # --- 3. transcrição -----------------------------------------------
        def on_progress(p: dict[str, Any]) -> None:
            payload = {k: v for k, v in p.items() if k != "phase"}
            emit("progress", **payload)

        result = self.engine.transcribe(
            audio_in,
            progress=on_progress,
            total_duration=info.duration or None,
            cancel=cancel,
        )
        result.source = str(source)
        emit("transcribed", duration=result.duration, elapsed=result.elapsed,
             segments=len(result.segments))

        # --- 4. artefatos -------------------------------------------------
        target_dir = Path(outdir) if outdir else self.outdir_for(source, job_id)
        fmts = tuple(formats or self.cfg.formats)
        emit("writing", formats=list(fmts), outdir=str(target_dir))
        outputs = self.write_outputs(result, target_dir, fmts, media=info)

        # limpa temporários
        with contextlib.suppress(OSError):
            shutil.rmtree(work, ignore_errors=True)
        emit("done", outdir=str(target_dir))

        return {
            "result": result,
            "outdir": str(target_dir),
            "outputs": outputs,
            "media": info,
            "primary_format": out_format,
        }

    # ------------------------------------------------------------------
    def write_outputs(self, result: TranscriptionResult, outdir: Path,
                      fmts: tuple[str, ...], *, media: MediaInfo | None = None) -> dict[str, str]:
        outdir.mkdir(parents=True, exist_ok=True)
        written: dict[str, str] = {}
        for f in fmts:
            if f not in fmt.RENDERERS:
                log.warning("formato ignorado: %s", f)
                continue
            text = fmt.render(f, result, self.cfg)
            path = outdir / f"transcricao.{f}"
            path.write_text(text, encoding="utf-8")
            written[f] = str(path)

        # cópia conveniente em .txt legível
        meta = {
            "source": str(result.source),
            "filename": Path(result.source).name,
            "model": result.model,
            "language": result.language,
            "language_probability": result.language_probability,
            "duration": result.duration,
            "elapsed": result.elapsed,
            "speed": result.speed,
            "segments": len(result.segments),
            "chars": len(result.text),
            "words": len(result.text.split()),
            "formats": list(written),
            "media": media.to_dict() if media else None,
            "created": time.time(),
        }
        (outdir / ".meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        written["meta"] = str(outdir / ".meta.json")
        return written
