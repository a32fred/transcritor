"""Motor de transcrição: wrapper fino e seguro sobre faster-whisper.

Responsabilidades:
  * carregar o modelo uma única vez (lazy) e reutilizá-lo;
  * rodar a transcrição emitindo progresso incremental;
  * serializar o resultado de forma estável (dict puro, pronto para JSON).

Nada aqui faz chamada de rede depois que o modelo está baixado.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import KNOWN_MODELS, Config

log = logging.getLogger("transcritor.engine")

ProgressFn = Callable[[dict[str, Any]], None]


@dataclass
class Segment:
    id: int
    start: float
    end: float
    text: str
    words: list[dict[str, Any]] = field(default_factory=list)
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id,
            "start": round(self.start, 3),
            "end": round(self.end, 3),
            "text": self.text,
        }
        if self.words:
            d["words"] = self.words
        return d


@dataclass
class TranscriptionResult:
    source: str
    model: str
    language: str
    language_probability: float
    duration: float
    duration_after_vad: float
    elapsed: float
    segments: list[Segment]

    @property
    def text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments if s.text.strip()).strip()

    @property
    def speed(self) -> float:
        """Fator de tempo real (xRT). >1 significa mais rápido que o áudio."""
        return (self.duration / self.elapsed) if self.elapsed > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "model": self.model,
            "language": self.language,
            "language_probability": round(self.language_probability, 4),
            "duration": round(self.duration, 3),
            "duration_after_vad": round(self.duration_after_vad, 3),
            "elapsed": round(self.elapsed, 3),
            "speed": round(self.speed, 3),
            "segment_count": len(self.segments),
            "text": self.text,
            "segments": [s.to_dict() for s in self.segments],
        }


class Engine:
    """Carrega o modelo sob demanda e transcreve. Thread-safe para 1 modelo."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._model = None
        self._model_key: tuple[str, str, str, int] | None = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self) -> Any:
        key = (
            self.cfg.local_model_path or self.cfg.model,
            self.cfg.device,
            self.cfg.compute_type,
            self.cfg.cpu_threads,
        )
        with self._lock:
            if self._model is not None and self._model_key == key:
                return self._model

            from faster_whisper import WhisperModel

            target = key[0]
            log.info("carregando modelo %s (device=%s, compute=%s, threads=%d)",
                     target, self.cfg.device, self.cfg.compute_type, self.cfg.cpu_threads)
            t0 = time.time()
            self._model = WhisperModel(
                target,
                device=self.cfg.device,
                compute_type=self.cfg.compute_type,
                cpu_threads=self.cfg.cpu_threads,
                download_root=str(self.cfg.models_dir),
                local_files_only=bool(self.cfg.local_model_path),
                num_workers=self.cfg.num_workers,
            )
            self._model_key = key
            log.info("modelo carregado em %.1fs", time.time() - t0)
            return self._model

    def unload(self) -> None:
        with self._lock:
            self._model = None
            self._model_key = None

    # ------------------------------------------------------------------
    def detect_language(self, path: str | Path) -> tuple[str, float]:
        """Detecta idioma com os 30 primeiros segundos (barato)."""
        model = self._load()
        _, info = model.transcribe(str(path), language=None, beam_size=1, vad_filter=True)
        return info.language, float(info.language_probability)

    # ------------------------------------------------------------------
    def transcribe(
        self,
        path: str | Path,
        *,
        progress: ProgressFn | None = None,
        total_duration: float | None = None,
        cancel: threading.Event | None = None,
        **overrides: Any,
    ) -> TranscriptionResult:
        """Transcreve um arquivo de áudio/vídeo.

        ``total_duration`` deve vir do ffprobe: a duração só é conhecida depois
        do fim do gerador, então passá-la adiante permite progresso percentual.
        """
        cfg = self.cfg
        source = str(path)
        model = self._load()

        kwargs = cfg.transcribe_kwargs()
        kwargs.update({k: v for k, v in overrides.items() if v is not None})

        t0 = time.time()
        segment_iter, info = model.transcribe(source, **kwargs)

        total = total_duration or float(getattr(info, "duration", 0.0) or 0.0)
        segments: list[Segment] = []
        last_emit = 0.0

        for i, seg in enumerate(segment_iter):
            if cancel is not None and cancel.is_set():
                log.warning("transcrição cancelada: %s", source)
                break

            words: list[dict[str, Any]] = []
            for w in (getattr(seg, "words", None) or []):
                words.append({
                    "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                    "word": w.word,
                    "probability": round(float(getattr(w, "probability", 0.0) or 0.0), 4),
                })

            segments.append(Segment(
                id=i,
                start=float(seg.start),
                end=float(seg.end),
                text=(seg.text or "").strip(),
                words=words,
                avg_logprob=float(getattr(seg, "avg_logprob", 0.0) or 0.0),
                no_speech_prob=float(getattr(seg, "no_speech_prob", 0.0) or 0.0),
            ))

            now = time.time()
            if progress and (now - last_emit >= 0.25 or i == 0):
                last_emit = now
                elapsed = now - t0
                pos = float(seg.end)
                pct = (pos / total * 100.0) if total else None
                remaining = None
                if total and pos > 0:
                    rate = pos / elapsed if elapsed else 0
                    remaining = max(0.0, (total - pos) / rate) if rate else None
                progress({
                    "phase": "transcribing",
                    "segment": i,
                    "position": round(pos, 2),
                    "duration": round(total, 2),
                    "percent": round(pct, 2) if pct is not None else None,
                    "elapsed": round(elapsed, 2),
                    "eta": round(remaining, 1) if remaining is not None else None,
                    "speed": round(pos / elapsed, 3) if elapsed else 0.0,
                    "preview": segments[-1].text[-160:] if segments else "",
                })

        elapsed = time.time() - t0

        return TranscriptionResult(
            source=source,
            model=cfg.model,
            language=(kwargs.get("language") or info.language or cfg.language or "pt"),
            language_probability=float(info.language_probability or 0.0),
            duration=float(info.duration or total or 0.0),
            duration_after_vad=float(getattr(info, "duration_after_vad", 0.0) or 0.0),
            elapsed=elapsed,
            segments=segments,
        )

    # ------------------------------------------------------------------
    def warmup(self) -> float:
        """Carrega o modelo e mede o tempo, para o watcher/web já responderem rápido."""
        t0 = time.time()
        self._load()
        return time.time() - t0


# --------------------------------------------------------------------------
# Utilidades de modelo
# --------------------------------------------------------------------------

def model_status(cfg: Config) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name, meta in KNOWN_MODELS.items():
        d = cfg.models_dir / f"faster-whisper-{name}"
        size = 0
        if d.exists():
            for p in d.rglob("*"):
                if p.is_file():
                    with contextlib.suppress(OSError):
                        size += p.stat().st_size
        out.append({
            "name": name,
            "repo": meta["repo"],
            "downloaded": (d / "model.bin").exists(),
            "path": str(d),
            "disk_mb": round(size / 1e6, 1),
            "approx_mb": meta["size_mb"],
            "quality": meta["quality"],
            "active": name == cfg.model,
        })
    return out


def download_model(name: str, cfg: Config, progress: ProgressFn | None = None) -> str:
    """Baixa (ou revalida) um modelo do Hugging Face para modelos_dir."""
    meta = KNOWN_MODELS.get(name)
    repo = meta["repo"] if meta else name
    target = cfg.models_dir / f"faster-whisper-{name if meta else name.split('/')[-1]}"
    target.mkdir(parents=True, exist_ok=True)

    from huggingface_hub import snapshot_download

    if progress:
        progress({"phase": "downloading", "model": name, "repo": repo, "path": str(target)})

    snapshot_download(
        repo_id=repo,
        local_dir=str(target),
        allow_patterns=[
            "config.json", "tokenizer.json", "vocabulary.*",
            "model.bin", "preprocessor_config.json",
        ],
    )
    if progress:
        progress({"phase": "downloaded", "model": name, "path": str(target)})
    return str(target)
