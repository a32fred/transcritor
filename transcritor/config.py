"""Configuração central do transcritor.

Ordem de precedência:
  1. Variáveis de ambiente (TRANSCRITOR_HOME, TRANSCRITOR_MODEL, ...)
  2. Arquivo config.json em $TRANSCRITOR_HOME/config.json
  3. Defaults deste módulo

O objetivo é transcrição PT-BR de máxima qualidade com pontuação nativa
do Whisper large-v3, em CPU, sem nenhuma chamada de rede após o download
do modelo.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# Caminhos
# --------------------------------------------------------------------------

DEFAULT_HOME = Path(os.environ.get("TRANSCRITOR_HOME", Path.home() / "transcritor")).expanduser()


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "sim"}


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# --------------------------------------------------------------------------
# Modelos conhecidos (repositórios CTranslate2 publicados pela Systran)
# --------------------------------------------------------------------------

KNOWN_MODELS: dict[str, dict[str, Any]] = {
    "tiny": {"repo": "Systran/faster-whisper-tiny", "size_mb": 75, "quality": "ruim"},
    "base": {"repo": "Systran/faster-whisper-base", "size_mb": 145, "quality": "fraca"},
    "small": {"repo": "Systran/faster-whisper-small", "size_mb": 484, "quality": "boa"},
    "medium": {"repo": "Systran/faster-whisper-medium", "size_mb": 1530, "quality": "muito boa"},
    "large-v2": {"repo": "Systran/faster-whisper-large-v2", "size_mb": 3090, "quality": "excelente"},
    "large-v3": {"repo": "Systran/faster-whisper-large-v3", "size_mb": 3090, "quality": "máxima"},
    "large-v3-turbo": {
        "repo": "deepdml/faster-whisper-large-v3-turbo-ct2",
        "size_mb": 1620,
        "quality": "excelente (rápido)",
    },
}

# Prompt inicial: instrui o modelo a pontuar em português do Brasil.
# O Whisper continua o estilo do prompt, então um texto bem pontuado
# melhora sensivelmente vírgulas, pontos e maiúsculas na saída.
DEFAULT_INITIAL_PROMPT = (
    "Transcrição em português do Brasil com pontuação completa: "
    "vírgulas, pontos finais, interrogações, exclamações e travessões. "
    "Nomes próprios e siglas são escritos corretamente e em maiúsculas."
)

AUDIO_EXTENSIONS = (
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".oga", ".opus",
    ".wma", ".aiff", ".aif", ".amr", ".mp4", ".mkv", ".mov", ".webm",
    ".m4v", ".avi", ".3gp", ".mpeg", ".mpg", ".ts", ".wmv",
)

VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi", ".ts", ".wmv", ".mpeg", ".mpg")

DEFAULT_FORMATS = ("txt", "srt", "vtt", "json", "md")


@dataclass
class Config:
    """Configuração completa, serializável em JSON."""

    # ---- caminhos -------------------------------------------------------
    home: Path = DEFAULT_HOME
    inbox: Path = field(default=None)          # type: ignore[assignment]
    outbox: Path = field(default=None)         # type: ignore[assignment]
    work: Path = field(default=None)           # type: ignore[assignment]
    models_dir: Path = field(default=None)     # type: ignore[assignment]
    state_file: Path = field(default=None)     # type: ignore[assignment]
    log_file: Path = field(default=None)       # type: ignore[assignment]

    # ---- motor ----------------------------------------------------------
    model: str = "large-v3"
    device: str = "cpu"                 # "cpu" | "cuda" | "auto"
    compute_type: str = "int8"          # int8 em CPU: ~4x mais rápido, perda mínima
    cpu_threads: int = 0                # 0 = usa nproc
    num_workers: int = 1
    beam_size: int = 5
    patience: float = 1.0
    length_penalty: float = 1.0
    repetition_penalty: float = 1.0
    no_repeat_ngram_size: int = 0
    temperatures: tuple[float, ...] = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
    compression_ratio_threshold: float = 2.4
    log_prob_threshold: float = -1.0
    no_speech_threshold: float = 0.6
    condition_on_previous_text: bool = True   # mantém contexto -> melhor pontuação
    prompt_reset_on_temperature: float = 0.5
    hallucination_silence_threshold: float = 2.0  # evita loops de alucinação
    word_timestamps: bool = False       # True melhora quebra de linha no SRT, piora um pouco a pontuação

    # ---- idioma / tarefa ------------------------------------------------
    language: str = "pt"
    task: str = "transcribe"            # "transcribe" | "translate"
    initial_prompt: str = DEFAULT_INITIAL_PROMPT
    hotwords: str = ""

    # ---- VAD (detecção de fala) -----------------------------------------
    vad: bool = True
    vad_threshold: float = 0.5
    vad_min_speech_ms: int = 250
    vad_min_silence_ms: int = 500
    vad_speech_pad_ms: int = 200

    # ---- saída ----------------------------------------------------------
    formats: tuple[str, ...] = DEFAULT_FORMATS
    txt_paragraph_gap: float = 1.6       # segundos de pausa -> novo parágrafo
    txt_max_chars_per_line: int = 0      # 0 = sem quebra forçada
    txt_timestamps: bool = False

    # ---- watcher --------------------------------------------------------
    watch_interval: float = 5.0
    watch_settle_seconds: float = 3.0    # arquivo precisa ficar estável por N s
    watch_workers: int = 1
    watch_delete_source: bool = False
    watch_include_video: bool = True

    # ---- web ------------------------------------------------------------
    web_host: str = "127.0.0.1"
    web_port: int = 8765

    # ---------------------------------------------------------------------
    def __post_init__(self) -> None:
        self.home = Path(self.home).expanduser()
        self.inbox = Path(self.inbox or self.home / "var" / "inbox")
        self.outbox = Path(self.outbox or self.home / "var" / "outbox")
        self.work = Path(self.work or self.home / "var" / "work")
        self.models_dir = Path(self.models_dir or self.home / "models")
        self.state_file = Path(self.state_file or self.home / "var" / "state.json")
        self.log_file = Path(self.log_file or self.home / "var" / "transcritor.log")

        self.formats = tuple(self.formats)
        self.temperatures = tuple(float(t) for t in self.temperatures)

        if self.cpu_threads <= 0:
            self.cpu_threads = max(1, (os.cpu_count() or 4) - 1)
        if self.device == "auto":
            self.device = "cpu"

    # ---- API de conveniência -------------------------------------------
    @property
    def model_dir(self) -> Path:
        return self.models_dir / f"faster-whisper-{self.model}"

    @property
    def local_model_path(self) -> str | None:
        """Caminho local do modelo, se já baixado. Caso contrário, None."""
        if self.model in KNOWN_MODELS:
            d = self.models_dir / f"faster-whisper-{self.model}"
            if (d / "model.bin").exists():
                return str(d)
        if "/" in self.model and Path(self.model).exists():
            return self.model
        return None

    def extensions(self) -> tuple[str, ...]:
        if self.watch_include_video:
            return AUDIO_EXTENSIONS
        return tuple(e for e in AUDIO_EXTENSIONS if e not in VIDEO_EXTENSIONS)

    def ensure_dirs(self) -> None:
        for d in (self.home, self.inbox, self.outbox, self.work, self.models_dir,
                  self.state_file.parent, self.log_file.parent):
            Path(d).mkdir(parents=True, exist_ok=True)

    # ---- carga / gravação ----------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["temperatures"] = list(self.temperatures)
        d["formats"] = list(self.formats)
        for k in ("home", "inbox", "outbox", "work", "models_dir", "state_file", "log_file"):
            d[k] = str(d[k])
        return d

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        home = DEFAULT_HOME
        path = Path(path).expanduser() if path else home / "config.json"
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                data = {}
        # remove chaves desconhecidas para não quebrar o dataclass
        valid = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        data = {k: v for k, v in data.items() if k in valid and not k.startswith("__")}
        # O default do campo `home` é congelado na definição da classe (import),
        # então precisamos reafirmar o DEFAULT_HOME atual — é ele que reflete
        # $TRANSCRITOR_HOME.
        data.setdefault("home", home)
        for k in ("temperatures", "formats"):
            if k in data and data[k] is not None:
                data[k] = tuple(data[k])

        cfg = cls(**data)

        # lembra de onde foi carregada para que save() escreva no mesmo lugar
        cfg._loaded_from = path

        # overrides por ambiente
        if os.environ.get("TRANSCRITOR_MODEL"):
            cfg.model = os.environ["TRANSCRITOR_MODEL"]
        if os.environ.get("TRANSCRITOR_LANGUAGE"):
            cfg.language = os.environ["TRANSCRITOR_LANGUAGE"]
        if os.environ.get("TRANSCRITOR_DEVICE"):
            cfg.device = os.environ["TRANSCRITOR_DEVICE"]
        if os.environ.get("TRANSCRITOR_COMPUTE_TYPE"):
            cfg.compute_type = os.environ["TRANSCRITOR_COMPUTE_TYPE"]
        cfg.watch_interval = float(os.environ.get("TRANSCRITOR_WATCH_INTERVAL", cfg.watch_interval))
        cfg.web_port = _env_int("TRANSCRITOR_WEB_PORT", cfg.web_port)
        cfg.web_host = os.environ.get("TRANSCRITOR_WEB_HOST", cfg.web_host)
        cfg.watch_delete_source = _env_bool("TRANSCRITOR_WATCH_DELETE", cfg.watch_delete_source)
        cfg.word_timestamps = _env_bool("TRANSCRITOR_WORD_TIMESTAMPS", cfg.word_timestamps)
        cfg.__post_init__()
        return cfg

    def save(self, path: Path | None = None) -> Path:
        path = Path(path).expanduser() if path else self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self._loaded_from = path
        return path

    @property
    def config_path(self) -> Path:
        """Arquivo de configuração efetivo (o que foi carregado, ou o padrão)."""
        return Path(getattr(self, "_loaded_from", None) or (self.home / "config.json"))

    def transcribe_kwargs(self) -> dict[str, Any]:
        """Parâmetros aceitos diretamente por WhisperModel.transcribe()."""
        return {
            "language": self.language or None,
            "task": self.task,
            "beam_size": self.beam_size,
            "patience": self.patience,
            "length_penalty": self.length_penalty,
            "repetition_penalty": self.repetition_penalty,
            "no_repeat_ngram_size": self.no_repeat_ngram_size,
            "temperature": list(self.temperatures),
            "compression_ratio_threshold": self.compression_ratio_threshold,
            "log_prob_threshold": self.log_prob_threshold,
            "no_speech_threshold": self.no_speech_threshold,
            "condition_on_previous_text": self.condition_on_previous_text,
            "prompt_reset_on_temperature": self.prompt_reset_on_temperature,
            "initial_prompt": self.initial_prompt or None,
            "word_timestamps": self.word_timestamps,
            "vad_filter": self.vad,
            "vad_parameters": {
                "threshold": self.vad_threshold,
                "min_speech_duration_ms": self.vad_min_speech_ms,
                "min_silence_duration_ms": self.vad_min_silence_ms,
                "speech_pad_ms": self.vad_speech_pad_ms,
            },
            "hallucination_silence_threshold": self.hallucination_silence_threshold,
            "hotwords": self.hotwords or None,
        }
