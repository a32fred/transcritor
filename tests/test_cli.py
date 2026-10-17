"""CLI: comandos, códigos de saída e efeitos colaterais em disco."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import transcritor.cli as cli_mod
import transcritor.config as config_mod
import transcritor.runner as runner_mod


@pytest.fixture
def cli(home, monkeypatch, engine_factory, tmp_path):
    """CLI apontada para uma home isolada, com o motor dublado."""
    monkeypatch.setattr(config_mod, "DEFAULT_HOME", home)
    monkeypatch.setattr(cli_mod, "Engine", engine_factory)
    monkeypatch.setattr(runner_mod, "Engine", engine_factory)
    return CliRunner()


def roda(cli, *args, **kw):
    return cli.invoke(cli_mod.cli, list(args), **kw)


# --------------------------------------------------------------------------
# Ajuda / descoberta
# --------------------------------------------------------------------------

def test_help(cli):
    r = roda(cli, "--help")
    assert r.exit_code == 0
    for comando in ("transcribe", "watch", "serve", "tui", "jobs", "models",
                    "config", "doctor", "info"):
        assert comando in r.output


def test_h_abrevia_help(cli):
    assert roda(cli, "-h").exit_code == 0


def test_version(cli):
    r = roda(cli, "--version")
    assert r.exit_code == 0
    from transcritor import __version__
    assert __version__ in r.output


def test_comando_inexistente_falha(cli):
    assert roda(cli, "transcrever").exit_code != 0


# --------------------------------------------------------------------------
# info / doctor / models / config
# --------------------------------------------------------------------------

def test_info_mostra_caminhos(cli, home):
    r = roda(cli, "info")
    assert r.exit_code == 0
    assert str(home) in r.output
    assert "modelo ativo" in r.output
    assert "entrada" in r.output
    assert "jobs" in r.output


def test_doctor_com_modelo_presente(cli, home):
    """Com o modelo 'baixado', o diagnóstico deve passar inteiro."""
    d = home / "models" / "faster-whisper-large-v3"
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.bin").write_bytes(b"x")

    r = roda(cli, "doctor")
    assert r.exit_code == 0, r.output
    assert "tudo certo" in r.output


def test_doctor_aponta_modelo_faltando(cli):
    r = roda(cli, "doctor")
    assert r.exit_code == 1
    assert "não baixado" in r.output
    assert "models download" in r.output


def test_models_list(cli, home):
    d = home / "models" / "faster-whisper-large-v3"
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.bin").write_bytes(b"x")

    r = roda(cli, "models", "list")
    assert r.exit_code == 0
    assert "large-v3" in r.output
    assert "tiny" in r.output
    assert "sim" in r.output, "modelo presente deve aparecer como baixado"


def test_models_use_persiste_escolha(cli, home):
    r = roda(cli, "models", "use", "medium")
    assert r.exit_code == 0

    cfg = json.loads((home / "config.json").read_text(encoding="utf-8"))
    assert cfg["model"] == "medium"


def test_config_show_json(cli, home):
    r = roda(cli, "config", "show", "--json")
    assert r.exit_code == 0
    d = json.loads(r.output)
    assert d["model"] == "large-v3"
    assert d["language"] == "pt"


def test_config_show_texto(cli):
    r = roda(cli, "config", "show")
    assert r.exit_code == 0
    assert "model" in r.output
    assert "inbox" in r.output


def test_config_set_escreve_arquivo(cli, home):
    r = roda(cli, "config", "set", "model=small", "beam_size=3")
    assert r.exit_code == 0

    d = json.loads((home / "config.json").read_text(encoding="utf-8"))
    assert d["model"] == "small"
    assert d["beam_size"] == 3


def test_config_set_converte_tipos(cli, home):
    roda(cli, "config", "set", "vad=false", "watch_interval=1.5", "formats=txt,json")
    d = json.loads((home / "config.json").read_text(encoding="utf-8"))
    assert d["vad"] is False
    assert d["watch_interval"] == 1.5
    assert d["formats"] == ["txt", "json"]


def test_config_set_chave_invalida(cli):
    r = roda(cli, "config", "set", "nao_existe=1")
    assert r.exit_code != 0
    assert "desconhecida" in r.output


def test_config_set_sem_igual(cli):
    r = roda(cli, "config", "set", "model")
    assert r.exit_code != 0
    assert "chave=valor" in r.output


def test_config_init_cria_arquivo(cli, home):
    r = roda(cli, "config", "init")
    assert r.exit_code == 0
    assert (home / "config.json").exists()


def test_config_init_nao_sobrescreve_sem_force(cli, home):
    (home / "config.json").write_text("{}", encoding="utf-8")
    r = roda(cli, "config", "init")
    assert r.exit_code != 0
    assert "já existe" in r.output


def test_config_init_force_sobrescreve(cli, home):
    alvo = home / "config.json"
    alvo.write_text("{}", encoding="utf-8")
    assert roda(cli, "config", "init", "--force").exit_code == 0
    assert "model" in json.loads(alvo.read_text(encoding="utf-8"))


def test_config_alternativo_e_respeitado(home, monkeypatch, engine_factory, tmp_path):
    monkeypatch.setattr(config_mod, "DEFAULT_HOME", home)
    monkeypatch.setattr(cli_mod, "Engine", engine_factory)

    alt = tmp_path / "meu.json"
    r = CliRunner().invoke(cli_mod.cli, ["--config", str(alt), "config", "set", "model=base"])
    assert r.exit_code == 0, r.output
    assert alt.exists()
    assert not (home / "config.json").exists(), "não pode gravar no caminho padrão"


# --------------------------------------------------------------------------
# transcribe
# --------------------------------------------------------------------------

def test_transcribe_ponta_a_ponta(cli, home, audio_file):
    r = roda(cli, "transcribe", str(audio_file))
    assert r.exit_code == 0, r.output
    assert "pronto" in r.output
    assert "prévia" in r.output

    pasta = home / "var" / "outbox" / "amostra"
    assert pasta.is_dir()
    for f in ("transcricao.txt", "transcricao.srt", "transcricao.json"):
        assert (pasta / f).exists()
    assert "Bom dia!" in (pasta / "transcricao.txt").read_text(encoding="utf-8")


def test_transcribe_respeita_pasta_de_saida(cli, audio_file, tmp_path):
    destino = tmp_path / "saidas"
    r = roda(cli, "transcribe", str(audio_file), "-o", str(destino))
    assert r.exit_code == 0, r.output
    assert (destino / "transcricao.txt").exists()


def test_transcribe_formatos_escolhidos(cli, home, audio_file):
    r = roda(cli, "transcribe", str(audio_file), "-f", "txt")
    assert r.exit_code == 0, r.output
    pasta = home / "var" / "outbox" / "amostra"
    assert (pasta / "transcricao.txt").exists()
    assert not (pasta / "transcricao.srt").exists()


def test_transcribe_stdout(cli, audio_file):
    r = roda(cli, "transcribe", str(audio_file), "--stdout")
    assert r.exit_code == 0, r.output
    assert "Bom dia!" in r.output


def test_transcribe_varios_arquivos(cli, home, audio_file, tmp_path):
    copia = tmp_path / "outro.wav"
    copia.write_bytes(Path(audio_file).read_bytes())

    r = roda(cli, "transcribe", str(audio_file), str(copia))
    assert r.exit_code == 0, r.output
    assert (home / "var" / "outbox" / "amostra").is_dir()
    assert (home / "var" / "outbox" / "outro").is_dir()


def test_transcribe_pasta_inteira(cli, home, tmp_path, audio_file):
    lote = tmp_path / "lote"
    lote.mkdir()
    (lote / "a.wav").write_bytes(Path(audio_file).read_bytes())
    (lote / "b.wav").write_bytes(Path(audio_file).read_bytes())

    r = roda(cli, "transcribe", str(lote))
    assert r.exit_code == 0, r.output
    assert (home / "var" / "outbox" / "a").is_dir()
    assert (home / "var" / "outbox" / "b").is_dir()


def test_transcribe_arquivo_sem_audio_falha_bonito(cli, tmp_path):
    ruim = tmp_path / "nao_audio.wav"
    ruim.write_text("texto", encoding="utf-8")

    r = roda(cli, "transcribe", str(ruim))
    assert r.exit_code == 1
    assert "falhou" in r.output
    assert "trilha de áudio" in r.output


def test_transcribe_arquivo_inexistente_e_erro_de_uso(cli):
    r = roda(cli, "transcribe", "/caminho/que/nao/existe.ogg")
    assert r.exit_code != 0


def test_transcribe_ignora_nao_midia(cli, tmp_path):
    texto = tmp_path / "notas.txt"
    texto.write_text("oi", encoding="utf-8")

    r = roda(cli, "transcribe", str(texto))
    assert r.exit_code != 0
    assert "ignorado" in r.output or "nenhum arquivo" in r.output


def test_transcribe_baixa_modelo_ausente_antes_de_transcrever(cli, audio_file, sem_download):
    """O modelo ausente é anunciado e baixado antes de transcrever.

    Regressão: antes, o faster-whisper baixava sozinho, dentro do carregamento
    do motor, sem aviso nem barra — até 3 GB de silêncio.
    """
    r = roda(cli, "transcribe", str(audio_file))
    assert r.exit_code == 0
    assert "baixando o modelo" in r.output
    assert "modelo pronto" in r.output
    assert sem_download.chamadas == ["large-v3"]


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------

def test_jobs_list_vazio(cli):
    r = roda(cli, "jobs", "list")
    assert r.exit_code == 0
    assert "sem jobs" in r.output


def test_jobs_list_mostra_jobs(cli, home, audio_file):
    from transcritor.config import Config
    from transcritor.jobs import JobStore

    cfg = Config(home=home, model="tiny")
    store = JobStore(cfg)
    job = store.add(audio_file, origin="cli")
    store.update(job.id, status="done", words=12, duration=3.0)

    r = roda(cli, "jobs", "list")
    assert r.exit_code == 0
    assert job.id in r.output
    assert "done" in r.output
    assert "amostra.wav" in r.output


def test_jobs_list_json(cli, home, audio_file):
    from transcritor.config import Config
    from transcritor.jobs import JobStore

    JobStore(Config(home=home, model="tiny")).add(audio_file)

    r = roda(cli, "jobs", "list", "--json")
    assert r.exit_code == 0
    assert json.loads(r.output)[0]["filename"] == "amostra.wav"


def test_jobs_list_filtra_por_status(cli, home, audio_file, tmp_path):
    from transcritor.config import Config
    from transcritor.jobs import JobStore

    store = JobStore(Config(home=home, model="tiny"))
    ok = store.add(audio_file)
    erro = store.add(tmp_path / "falhou.ogg")
    store.update(ok.id, status="done")
    store.update(erro.id, status="error", error="quebrou")

    r = roda(cli, "jobs", "list", "--status", "error", "--json")
    d = json.loads(r.output)
    assert len(d) == 1
    assert d[0]["filename"] == "falhou.ogg"


def test_jobs_show(cli, home, audio_file, tmp_path):
    from transcritor.config import Config
    from transcritor.jobs import JobStore

    store = JobStore(Config(home=home, model="tiny"))
    job = store.add(audio_file)
    saida = tmp_path / "transcricao.txt"
    saida.write_text("conteúdo transcrito", encoding="utf-8")
    store.update(job.id, status="done", outputs={"txt": str(saida)})

    r = roda(cli, "jobs", "show", job.id)
    assert r.exit_code == 0
    assert "conteúdo transcrito" in r.output


def test_jobs_show_inexistente(cli):
    r = roda(cli, "jobs", "show", "naoexiste")
    assert r.exit_code != 0
    assert "não encontrado" in r.output


def test_jobs_rm(cli, home, audio_file):
    from transcritor.config import Config
    from transcritor.jobs import JobStore

    job = JobStore(Config(home=home, model="tiny")).add(audio_file)
    r = roda(cli, "jobs", "rm", job.id)
    assert r.exit_code == 0
    assert "removido" in r.output


def test_jobs_retry_refaz_job_com_erro(cli, home, audio_file):
    from transcritor.config import Config
    from transcritor.jobs import JobStore

    store = JobStore(Config(home=home, model="tiny"))
    job = store.add(audio_file, origin="cli")
    store.update(job.id, status="error", error="quebrou antes")

    r = roda(cli, "jobs", "retry", job.id)
    assert r.exit_code == 0, r.output
    assert "1/1 concluído" in r.output

    atualizado = JobStore(Config(home=home, model="tiny")).get(job.id)
    assert atualizado.status == "done"
    assert atualizado.error == ""
    assert atualizado.words > 0
    assert Path(atualizado.outputs["txt"]).exists()


def test_jobs_retry_failed_em_lote(cli, home, audio_file, tmp_path):
    from transcritor.config import Config
    from transcritor.jobs import JobStore

    copia = tmp_path / "outro.wav"
    copia.write_bytes(Path(audio_file).read_bytes())

    store = JobStore(Config(home=home, model="tiny"))
    a = store.add(audio_file)
    b = store.add(copia)
    store.update(a.id, status="error", error="x")
    store.update(b.id, status="error", error="y")

    r = roda(cli, "jobs", "retry", "--failed", "--yes")
    assert r.exit_code == 0, r.output
    assert "2/2 concluído" in r.output


def test_jobs_retry_failed_sem_erros(cli):
    r = roda(cli, "jobs", "retry", "--failed", "--yes")
    assert r.exit_code == 0
    assert "nenhum job com erro" in r.output


def test_jobs_retry_sem_argumento(cli):
    r = roda(cli, "jobs", "retry")
    assert r.exit_code != 0
    assert "job_id" in r.output


def test_jobs_retry_arquivo_original_sumiu(cli, home, tmp_path):
    from transcritor.config import Config
    from transcritor.jobs import JobStore

    sumido = tmp_path / "sumido.ogg"
    sumido.write_bytes(b"x")
    store = JobStore(Config(home=home, model="tiny"))
    job = store.add(sumido)
    store.update(job.id, status="error")
    sumido.unlink()

    r = roda(cli, "jobs", "retry", job.id)
    assert r.exit_code != 0
    assert "sumiu" in r.output


def test_jobs_clear(cli, home, audio_file):
    from transcritor.config import Config
    from transcritor.jobs import JobStore

    store = JobStore(Config(home=home, model="tiny"))
    job = store.add(audio_file)
    store.update(job.id, status="done")

    r = roda(cli, "jobs", "clear", "--yes")
    assert r.exit_code == 0
    assert "1 job" in r.output


# --------------------------------------------------------------------------
# detect
# --------------------------------------------------------------------------

def test_detect_mostra_idioma(cli, audio_file):
    r = roda(cli, "detect", str(audio_file))
    assert r.exit_code == 0, r.output
    assert "pt" in r.output
    assert "confiança" in r.output


# --------------------------------------------------------------------------
# watch --once
# --------------------------------------------------------------------------

def test_watch_once_processa_a_inbox(cli, home, audio_file):
    caixa = home / "var" / "inbox"
    (caixa / "chegou.wav").write_bytes(Path(audio_file).read_bytes())

    r = roda(cli, "watch", "--once")
    assert r.exit_code == 0, r.output
    assert "fim: 1 concluído(s)" in r.output, r.output
    assert (home / "var" / "outbox" / "chegou" / "transcricao.txt").exists()


def test_watch_once_sem_arquivos(cli):
    r = roda(cli, "watch", "--once")
    assert r.exit_code == 0, r.output
    assert "fim: 0 concluído(s)" in r.output


def test_watch_once_nao_processa_duas_vezes(cli, home, audio_file):
    caixa = home / "var" / "inbox"
    (caixa / "unico.wav").write_bytes(Path(audio_file).read_bytes())

    assert roda(cli, "watch", "--once").exit_code == 0
    assert roda(cli, "watch", "--once").exit_code == 0

    from transcritor.config import Config
    from transcritor.jobs import JobStore

    assert JobStore(Config(home=home, model="tiny")).counts()["total"] == 1
