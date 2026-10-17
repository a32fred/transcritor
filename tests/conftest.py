"""Fixtures compartilhadas da suíte de testes.

Nenhum teste baixa modelo nem depende de GPU: o motor de transcrição é
substituído por um dublê determinístico (``FakeEngine``) que devolve
segmentos conhecidos. Só os testes de mídia exigem ``ffmpeg`` de verdade,
e são pulados automaticamente se ele não estiver instalado.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from transcritor.config import Config
from transcritor.engine import Segment, TranscriptionResult

# --------------------------------------------------------------------------
# Áudio de exemplo
# --------------------------------------------------------------------------

# Frases curtas, com pontuação variada, para exercitar o agrupamento em
# parágrafos e a quebra de legendas.
FAKE_SEGMENTS = [
    (0.00, 2.50, "Bom dia! Hoje é sábado, 17 de outubro de 2026."),
    (4.20, 6.10, "Você sabia que a transcrição pode ser feita offline?"),
    (6.10, 9.00, "Sim, sem enviar nada para a nuvem."),
    (12.00, 15.50, "O sistema usa o modelo Whisper, rodando localmente."),
]


def make_segments() -> list[Segment]:
    return [
        Segment(id=i, start=s, end=e, text=t)
        for i, (s, e, t) in enumerate(FAKE_SEGMENTS)
    ]


class FakeDownload:
    """Substitui ``download_model``: cria um ``model.bin`` de mentira.

    O sistema real baixa de 1,5 a 3 GB do Hugging Face. Nenhum teste pode
    encostar na rede, então todos os pontos de entrada (CLI, runner e web)
    recebem este dublê pela fixture ``sem_download``.
    """

    def __init__(self) -> None:
        self.chamadas: list[str] = []

    def __call__(self, name, cfg, progress=None):
        self.chamadas.append(name)
        destino = cfg.models_dir / f"faster-whisper-{name}"
        destino.mkdir(parents=True, exist_ok=True)
        (destino / "model.bin").write_bytes(b"modelo de mentira")
        if progress:
            base = {"model": name, "repo": "fake/repo", "path": str(destino), "expected_mb": 100.0}
            progress({**base, "phase": "downloading", "downloaded_mb": 50.0, "percent": 50.0})
            progress({**base, "phase": "downloaded", "downloaded_mb": 100.0, "percent": 100.0})
        return str(destino)


class FakeEngine:
    """Dublê do ``Engine`` com a mesma interface pública usada pelo sistema."""

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg
        self.loaded = False
        self.calls: list[str] = []
        self.fail_with: Exception | None = None
        self.detect: tuple[str, float] = ("pt", 0.99)

    # -- interface ------------------------------------------------------
    def warmup(self) -> float:
        self.loaded = True
        return 0.01

    def unload(self) -> None:
        self.loaded = False

    def detect_language(self, path) -> tuple[str, float]:
        return self.detect

    def transcribe(self, path, *, progress=None, total_duration=None,
                   cancel=None, **overrides) -> TranscriptionResult:
        self.loaded = True
        self.calls.append(str(path))
        if self.fail_with is not None:
            raise self.fail_with

        segments = make_segments()
        total = total_duration or 15.5
        if progress:
            for seg in segments:
                progress({
                    "phase": "transcribing",
                    "segment": seg.id,
                    "position": seg.end,
                    "duration": total,
                    "percent": round(seg.end / total * 100, 2),
                    "elapsed": seg.end / 2,
                    "eta": max(0.0, (total - seg.end) / 2),
                    "speed": 2.0,
                    "preview": seg.text,
                })

        return TranscriptionResult(
            source=str(path),
            model="fake-model",
            language="pt",
            language_probability=0.99,
            duration=total,
            duration_after_vad=total - 1.0,
            elapsed=total / 2,
            segments=segments,
        )


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def home(tmp_path: Path) -> Path:
    """Diretório ``TRANSCRITOR_HOME`` isolado por teste."""
    h = tmp_path / "transcritor-home"
    for sub in ("var/inbox", "var/outbox", "var/work", "models"):
        (h / sub).mkdir(parents=True)
    return h


@pytest.fixture
def cfg(home: Path) -> Config:
    c = Config(home=home, model="tiny", language="pt")
    c.ensure_dirs()
    return c


@pytest.fixture
def engine(cfg: Config) -> FakeEngine:
    return FakeEngine(cfg)


@pytest.fixture
def engine_factory():
    """A classe dublê, para quem precisa substituir ``Engine`` no módulo."""
    return FakeEngine


@pytest.fixture(autouse=True)
def sem_download(monkeypatch):
    """Nenhum teste baixa modelo: o download vira um dublê em todos os módulos.

    É automático de propósito. Um único caminho esquecido (CLI, runner ou web)
    faria a suíte tentar baixar gigabytes do Hugging Face.
    """
    import transcritor.cli as cli_mod
    import transcritor.runner as runner_mod
    import transcritor.web.server as web_server_mod

    fake = FakeDownload()
    for modulo in (cli_mod, runner_mod, web_server_mod):
        monkeypatch.setattr(modulo, "download_model", fake, raising=False)
    return fake


@pytest.fixture
def result() -> TranscriptionResult:
    return TranscriptionResult(
        source="/tmp/exemplo.ogg",
        model="fake-model",
        language="pt",
        language_probability=0.9876,
        duration=15.5,
        duration_after_vad=14.5,
        elapsed=8.25,
        segments=make_segments(),
    )


@pytest.fixture
def audio_file(tmp_path: Path, request) -> Path:
    """Gera um WAV curto com ffmpeg. Pula o teste se o ffmpeg não existir."""
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg não está instalado")

    import subprocess

    out = tmp_path / "amostra.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-ar", "16000", "-ac", "1", "-y", str(out)],
        check=True, capture_output=True,
    )
    return out
