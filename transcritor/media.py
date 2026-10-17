"""Inspeção e normalização de mídia via ffmpeg/ffprobe."""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .config import AUDIO_EXTENSIONS

if TYPE_CHECKING:  # numpy só é importado na hora de decodificar
    import numpy as np


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


def decode_pcm(src: str | Path, *, sample_rate: int = 16000,
               timeout: int = 3600) -> np.ndarray:
    """Decodifica o áudio para amostras float32, mono, em ``sample_rate``.

    É o que o Whisper espera na entrada (ou seja, o mesmo formato que o
    ``decode_audio`` do faster-whisper produziria): inteiro de 16 bits
    dividido por 32768. A diferença é quem decodifica — aqui é o ffmpeg, que
    o transcritor já exige de qualquer forma, em vez do PyAV.

    Isso tem duas consequências boas: o ffmpeg lê muito mais formatos do que
    uma biblioteca só, e a transcrição deixa de depender da API do PyAV, que
    muda entre versões. A versão 19 removeu o argumento ``metadata_errors``
    que o faster-whisper (1.2.1) ainda passa, e qualquer pessoa que instalasse
    hoje, no Python 3.12 ou mais novo, quebrava na primeira transcrição.
    Passando as amostras prontas, o faster-whisper nem entra nesse caminho.
    """
    ffmpeg = ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError("ffmpeg não encontrado no PATH")

    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", str(src),
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-c:a", "pcm_s16le",
        "-f", "s16le",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    if proc.returncode != 0:
        linhas = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        motivo = linhas[-1].strip() if linhas else "erro desconhecido"
        raise RuntimeError(
            f"não foi possível decodificar {Path(src).name} com o ffmpeg: {motivo}"
        )

    import numpy as np

    amostras = np.frombuffer(proc.stdout, dtype="<i2")
    if amostras.size == 0:
        raise RuntimeError(f"nenhuma amostra de áudio em {Path(src).name}")
    return amostras.astype(np.float32) / 32768.0


def duration(path: str | Path) -> float:
    return probe(path).duration
