"""Configuração: defaults, precedência, serialização e caminhos."""

from __future__ import annotations

import json
from pathlib import Path

from transcritor.config import (
    AUDIO_EXTENSIONS,
    DEFAULT_INITIAL_PROMPT,
    KNOWN_MODELS,
    Config,
)


def test_defaults_de_qualidade():
    """Os padrões precisam refletir a promessa do README: PT-BR, pontuação, large-v3."""
    c = Config()
    assert c.model == "large-v3"
    assert c.language == "pt"
    assert c.task == "transcribe"
    assert c.beam_size == 5
    assert c.compute_type == "int8"
    # contexto entre segmentos e VAD são o que sustenta a pontuação boa
    assert c.condition_on_previous_text is True
    assert c.vad is True
    assert c.initial_prompt == DEFAULT_INITIAL_PROMPT
    assert "português do Brasil" in c.initial_prompt


def test_termos_do_prompt_melhoram_pontuacao():
    """O prompt inicial deve mencionar os sinais de pontuação que queremos."""
    for marca in ("vírgulas", "pontos", "interrogações", "exclamações"):
        assert marca in DEFAULT_INITIAL_PROMPT.lower()


def test_diretorios_derivados_da_home(tmp_path: Path):
    home = tmp_path / "T"
    c = Config(home=home)
    assert c.inbox == home / "var" / "inbox"
    assert c.outbox == home / "var" / "outbox"
    assert c.state_file == home / "var" / "state.json"
    assert c.models_dir == home / "models"


def test_cpu_threads_automatico():
    import os
    c = Config()
    assert c.cpu_threads >= 1
    assert c.cpu_threads == max(1, (os.cpu_count() or 4) - 1)


def test_device_auto_vira_cpu():
    assert Config(device="auto").device == "cpu"


def test_ensure_dirs_cria_arvore(tmp_path: Path):
    c = Config(home=tmp_path / "novo")
    c.ensure_dirs()
    for d in (c.inbox, c.outbox, c.work, c.models_dir):
        assert d.is_dir()


def test_roundtrip_save_load(tmp_path: Path):
    c = Config(home=tmp_path / "h", model="medium", beam_size=3, language="es")
    path = c.save()
    assert path.exists()

    carregado = Config.load(path)
    assert carregado.model == "medium"
    assert carregado.beam_size == 3
    assert carregado.language == "es"


def test_load_ignora_chaves_desconhecidas(tmp_path: Path):
    """Um config de versão futura não pode quebrar o carregamento."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "model": "small",
        "chave_que_nao_existe": 42,
        "__comentario__": "oi",
    }), encoding="utf-8")
    c = Config.load(path)
    assert c.model == "small"


def test_load_arquivo_corrompido_usa_padroes(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text("{ isso não é json", encoding="utf-8")
    assert Config.load(path).model == "large-v3"


def test_config_lembra_de_onde_veio(tmp_path: Path, monkeypatch):
    """Regressão: `--config outro.json` precisa gravar no mesmo arquivo."""
    import transcritor.config as config_mod

    home = tmp_path / "casa"
    monkeypatch.setattr(config_mod, "DEFAULT_HOME", home)

    alt = tmp_path / "alternativo.json"
    assert not alt.exists()

    c = config_mod.Config.load(alt)
    assert c.config_path == alt
    c.save()

    assert alt.exists(), "save() tem que respeitar o arquivo de origem"
    assert not (home / "config.json").exists(), "não pode gravar no caminho padrão"
    assert json.loads(alt.read_text(encoding="utf-8"))["model"] == c.model


def test_save_atualiza_o_caminho(tmp_path: Path):
    c = Config(home=tmp_path / "h")
    destino = tmp_path / "novo.json"
    c.save(destino)
    assert c.config_path == destino


def test_variaveis_de_ambiente_tem_precedencia(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TRANSCRITOR_MODEL", "base")
    monkeypatch.setenv("TRANSCRITOR_LANGUAGE", "en")
    monkeypatch.setenv("TRANSCRITOR_DEVICE", "cuda")
    monkeypatch.setenv("TRANSCRITOR_WEB_PORT", "9999")

    c = Config.load(tmp_path / "inexistente.json")
    assert c.model == "base"
    assert c.language == "en"
    assert c.device == "cuda"
    assert c.web_port == 9999


def test_transcribe_kwargs_contem_o_essencial():
    kw = Config().transcribe_kwargs()
    for chave in ("language", "task", "beam_size", "temperature",
                  "initial_prompt", "vad_filter", "condition_on_previous_text",
                  "hallucination_silence_threshold"):
        assert chave in kw, f"faltou {chave} em transcribe_kwargs()"
    assert kw["vad_filter"] is True
    assert isinstance(kw["temperature"], list)


def test_extensoes_cobrem_ogg_e_video():
    c = Config()
    exts = c.extensions()
    for e in (".ogg", ".oga", ".opus", ".mp3", ".wav", ".m4a", ".flac"):
        assert e in exts, f"{e} deveria ser aceito"
    for e in (".mp4", ".mkv", ".mov", ".webm"):
        assert e in exts, f"{e} (vídeo) deveria ser aceito"


def test_watch_include_video_false_remove_video():
    exts = Config(watch_include_video=False).extensions()
    assert ".ogg" in exts
    assert ".mp4" not in exts
    assert ".mkv" not in exts


def test_extensoes_sem_duplicatas():
    assert len(set(AUDIO_EXTENSIONS)) == len(AUDIO_EXTENSIONS)


def test_modelos_conhecidos_tem_repo_e_tamanho():
    for nome, meta in KNOWN_MODELS.items():
        assert "repo" in meta and "/" in meta["repo"], nome
        assert meta["size_mb"] > 0, nome
    assert "large-v3" in KNOWN_MODELS


def test_local_model_path_inexistente_e_none(cfg: Config):
    assert cfg.local_model_path is None


def test_local_model_path_detecta_modelo_baixado(cfg: Config):
    d = cfg.models_dir / f"faster-whisper-{cfg.model}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.bin").write_bytes(b"x")
    assert cfg.local_model_path == str(d)


def test_formats_e_temperatures_viram_tuplas(tmp_path: Path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"formats": ["txt"], "temperatures": [0.0, 0.5]}), encoding="utf-8")
    c = Config.load(path)
    assert c.formats == ("txt",)
    assert c.temperatures == (0.0, 0.5)


def test_to_dict_e_serializavel(cfg: Config):
    d = cfg.to_dict()
    assert isinstance(d["home"], str)
    assert isinstance(d["formats"], list)
    json.dumps(d)  # não pode levantar


# --------------------------------------------------------------------------
# Onde ficam os dados (código e dados separados)
# --------------------------------------------------------------------------

def test_home_padrao_vai_para_o_xdg_quando_e_instalacao_nova(monkeypatch, tmp_path: Path):
    """Instalado pelo pipx não existe pasta de código: dados vão para o XDG."""
    import transcritor.config as config_mod

    casa = tmp_path / "casa"
    casa.mkdir()
    monkeypatch.delenv("TRANSCRITOR_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "dados"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: casa))

    assert config_mod._home_padrao() == tmp_path / "dados" / "transcritor"


def test_home_padrao_sem_xdg_usa_local_share(monkeypatch, tmp_path: Path):
    import transcritor.config as config_mod

    casa = tmp_path / "casa"
    casa.mkdir()
    monkeypatch.delenv("TRANSCRITOR_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: casa))

    assert config_mod._home_padrao() == casa / ".local" / "share" / "transcritor"


def test_home_padrao_respeita_layout_antigo(monkeypatch, tmp_path: Path):
    """Quem já tem `~/transcritor/var` continua ali: nada é movido nem perdido."""
    import transcritor.config as config_mod

    casa = tmp_path / "casa"
    (casa / "transcritor" / "var" / "outbox").mkdir(parents=True)
    monkeypatch.delenv("TRANSCRITOR_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "dados"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: casa))

    assert config_mod._home_padrao() == casa / "transcritor"


def test_home_padrao_respeita_config_antigo(monkeypatch, tmp_path: Path):
    """Basta existir o config.json antigo para o layout antigo valer."""
    import transcritor.config as config_mod

    casa = tmp_path / "casa"
    (casa / "transcritor").mkdir(parents=True)
    (casa / "transcritor" / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("TRANSCRITOR_HOME", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "dados"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: casa))

    assert config_mod._home_padrao() == casa / "transcritor"


def test_ambiente_vence_na_resolucao_do_home(monkeypatch, tmp_path: Path):
    import transcritor.config as config_mod

    monkeypatch.setenv("TRANSCRITOR_HOME", str(tmp_path / "escolhido"))
    assert config_mod.resolver_home() == tmp_path / "escolhido"

    monkeypatch.delenv("TRANSCRITOR_HOME")
    assert config_mod.resolver_home() == config_mod.DEFAULT_HOME


def test_load_usa_o_home_resolvido_agora(monkeypatch, tmp_path: Path):
    """Regressão: config.json do home tem que refletir a decisão do momento."""
    escolhido = tmp_path / "outro-lugar"
    escolhido.mkdir()
    monkeypatch.setenv("TRANSCRITOR_HOME", str(escolhido))

    c = Config.load()
    assert c.home == escolhido
    assert c.inbox == escolhido / "var" / "inbox"
