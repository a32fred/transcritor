"""Inspeção e normalização de mídia via ffmpeg/ffprobe."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import AUDIO_EXTENSIONS


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    has_audio: bool = False
    has_video: bool = False
    audio_codec: str = ""
    sample_rate: int = 0
    channels: int = 0
    size_bytes: int = 0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "duration": round(self.duration, 3),
            "has_audio": self.has_audio,
            "has_video": self.has_video,
            "audio_codec": self.audio_codec,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "size_bytes": self.size_bytes,
        }


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


def is_media_file(path: str | Path) -> bool:
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def probe(path: str | Path) -> MediaInfo:
    """Lê metadados com ffprobe. Nunca levanta exceção: retorna o que conseguiu."""
    p = Path(path)
    info = MediaInfo(path=str(p))
    with contextlib.suppress(OSError):
        info.size_bytes = p.stat().st_size

    ffprobe = ffprobe_path()
    if not ffprobe:
        return info

    cmd = [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(p),
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        data = json.loads(out.stdout or "{}")
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return info

    fmt = data.get("format") or {}
    try:
        info.duration = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        info.duration = 0.0

    for st in data.get("streams") or []:
        kind = st.get("codec_type")
        if kind == "audio" and not info.has_audio:
            info.has_audio = True
            info.audio_codec = st.get("codec_name") or ""
            try:
                info.sample_rate = int(st.get("sample_rate") or 0)
            except (TypeError, ValueError):
                info.sample_rate = 0
            info.channels = int(st.get("channels") or 0)
            if not info.duration:
                with contextlib.suppress(TypeError, ValueError):
                    info.duration = float(st.get("duration") or 0.0)
        elif kind == "video":
            # capas de álbum aparecem como vídeo de 1 frame: ignora
            if st.get("disposition", {}).get("attached_pic"):
                continue
            info.has_video = True

    return info


def normalize(src: str | Path, dst: str | Path, *, sample_rate: int = 16000,
              loudnorm: bool = False, timeout: int = 3600) -> Path:
    """Converte qualquer entrada para WAV mono 16 kHz (formato ideal do Whisper).

    Usa ffmpeg. Se o filtro loudnorm estiver habilitado, aplica normalização
    de volume — útil para gravações com nível muito baixo.
    """
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg não encontrado no PATH")

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    filters = [f"aresample={sample_rate}"]
    if loudnorm:
        filters.insert(0, "loudnorm=I=-16:TP=-1.5:LRA=11")

    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(src),
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-af", ",".join(filters),
        "-c:a", "pcm_s16le",
        "-y", str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)
    return dst


def duration(path: str | Path) -> float:
    return probe(path).duration
