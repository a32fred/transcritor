"""Instalação e primeiro uso: menu, modelo guiado, ícone do menu e "Abrir com".

Esta é a superfície que decide se alguém sem conhecimento técnico consegue
usar o programa. Os testes aqui são de comportamento observável: o que aparece
na tela, o que é gravado em disco e o que deixa de ser exigido do usuário.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import click
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


def fingir_terminal(monkeypatch, valor: bool = True) -> None:
    """O CliRunner não é um terminal; o menu só aparece se isso for fingido."""
    monkeypatch.setattr(cli_mod, "_tem_terminal", lambda: valor)


# --------------------------------------------------------------------------
# Menu: transcrever sem decorar comando
# --------------------------------------------------------------------------

def test_sem_comando_em_terminal_abre_o_menu(cli, monkeypatch):
    fingir_terminal(monkeypatch)
    r = roda(cli, input="0\n")
    assert r.exit_code == 0
    assert "transcrever um arquivo ou uma pasta" in r.output
    assert "abrir a interface no navegador" in r.output
    assert "baixar ou trocar o modelo" in r.output
    assert "conferir a instalação" in r.output


def test_sem_comando_e_sem_terminal_mostra_a_ajuda(cli):
    """Em pipe/script o menu não faz sentido: aí vale a ajuda de sempre."""
    r = roda(cli)
    assert r.exit_code == 0
    assert "Commands:" in r.output
    assert "transcribe" in r.output


def test_menu_nao_depende_de_decorar_o_nome_do_comando(cli, monkeypatch):
    """A opção 4 do menu roda o mesmo doctor, com as mesmas checagens."""
    fingir_terminal(monkeypatch)
    r = roda(cli, input="4\n0\n")
    assert r.exit_code == 0
    assert "doctor" in r.output
    assert "python" in r.output


def test_menu_transcrever_aceita_caminho_com_espaco(cli, home, monkeypatch,
                                                    audio_file, tmp_path):
    """Arrastar um arquivo com espaço no nome é o caso comum, não a exceção."""
    # O modelo já está baixado: o teste é sobre o caminho com espaço, não sobre
    # o download (que teria a própria pergunta no meio do menu).
    (home / "config.json").write_text(json.dumps({"model": "tiny"}), encoding="utf-8")
    baixado = home / "models" / "faster-whisper-tiny"
    baixado.mkdir(parents=True)
    (baixado / "model.bin").write_bytes(b"modelo de mentira")

    com_espaco = tmp_path / "meu áudio de teste.wav"
    shutil.copy2(audio_file, com_espaco)
    fingir_terminal(monkeypatch)

    r = roda(cli, input=f"1\n{com_espaco}\n0\n")
    assert r.exit_code == 0
    saidas = list((home / "var" / "outbox").rglob("*.txt"))
    assert saidas, f"nada foi transcrito; saída do menu:\n{r.output}"


def test_menu_transcrever_avisa_quando_o_caminho_nao_existe(cli, monkeypatch):
    fingir_terminal(monkeypatch)
    r = roda(cli, input="1\n/nao/existe/mesmo.wav\n0\n")
    assert r.exit_code == 0
    assert "não encontrei" in r.output


# --------------------------------------------------------------------------
# Modelo ausente: avisar, explicar e medir o download
# --------------------------------------------------------------------------

def test_garantir_modelo_baixa_quando_falta(cfg, sem_download):
    cli_mod.garantir_modelo(cfg)
    assert sem_download.chamadas == ["tiny"]


def test_garantir_modelo_nao_baixa_o_que_ja_existe(cfg, sem_download):
    destino = cfg.models_dir / "faster-whisper-tiny"
    destino.mkdir(parents=True)
    (destino / "model.bin").write_bytes(b"ja estou aqui")

    cli_mod.garantir_modelo(cfg)
    assert sem_download.chamadas == []


def test_garantir_modelo_anuncia_o_tamanho_antes(cfg, capsys):
    """O usuário precisa saber o tamanho antes de baixar."""
    cli_mod.garantir_modelo(cfg)
    saida = capsys.readouterr().out
    assert "baixando o modelo tiny" in saida
    assert "75 MB" in saida
    assert "primeira vez" in saida


def test_garantir_modelo_erro_diz_o_que_fazer(cfg, monkeypatch):
    def sem_internet(*a, **kw):
        raise OSError("sem internet")

    monkeypatch.setattr(cli_mod, "download_model", sem_internet)
    with pytest.raises(click.ClickException) as erro:
        cli_mod.garantir_modelo(cfg)
    mensagem = str(erro.value)
    assert "sem internet" in mensagem
    assert "models download" in mensagem


def test_escolher_modelo_lista_tamanhos_e_grava(cfg, monkeypatch, capsys):
    monkeypatch.setattr(cli_mod.click, "prompt", lambda *a, **kw: "large-v3-turbo")

    assert cli_mod.escolher_modelo(cfg) is True
    assert cfg.model == "large-v3-turbo"

    saida = capsys.readouterr().out
    assert "large-v3-turbo" in saida and "1,6 GB" in saida
    assert "small" in saida and "484 MB" in saida

    salvo = json.loads((cfg.home / "config.json").read_text(encoding="utf-8"))
    assert salvo["model"] == "large-v3-turbo", "a escolha tem que sobreviver ao comando"


def test_escolher_modelo_cancelado_nao_muda_nada(cfg, monkeypatch):
    monkeypatch.setattr(cli_mod.click, "prompt",
                        lambda *a, **kw: (_ for _ in ()).throw(click.Abort()))
    antes = cfg.model
    assert cli_mod.escolher_modelo(cfg) is False
    assert cfg.model == antes


def test_tamanho_legivel_usa_virgula_decimal():
    assert cli_mod._tamanho_legivel(484) == "484 MB"
    assert cli_mod._tamanho_legivel(1530) == "1,5 GB"
    assert cli_mod._tamanho_legivel(3090) == "3,1 GB"


def test_progresso_do_modelo_nao_polui_saida_sem_terminal(capsys):
    """Em pipe (cron, script) a barra de progresso não deve virar lixo."""
    cli_mod._progresso_modelo({"phase": "downloading", "percent": 42.0,
                               "downloaded_mb": 10.0, "expected_mb": 24.0})
    assert capsys.readouterr().out == ""


# --------------------------------------------------------------------------
# abrir: o caminho de quem não vai digitar comando
# --------------------------------------------------------------------------

def test_abrir_usa_os_arquivos_no_lugar_de_copia_los(cli, audio_file, monkeypatch):
    enviados: list[str] = []
    abertos: list[str] = []

    monkeypatch.setattr(cli_mod, "_servidor_no_ar", lambda *a, **kw: True)
    monkeypatch.setattr(cli_mod, "_enfileirar_pela_api",
                        lambda host, port, arquivos: (enviados.extend(a.name for a in arquivos),
                                                      len(arquivos))[-1])
    monkeypatch.setattr(cli_mod.webbrowser, "open", abertos.append)

    r = roda(cli, "abrir", str(audio_file))
    assert r.exit_code == 0
    assert enviados == [audio_file.name]
    assert abertos and abertos[0].startswith("http://127.0.0.1:")


def test_abrir_sobe_o_servidor_quando_ainda_nao_esta_no_ar(cli, monkeypatch):
    subiu: list[bool] = []
    monkeypatch.setattr(cli_mod, "_servidor_no_ar", lambda *a, **kw: False)
    monkeypatch.setattr(cli_mod, "_subir_servidor", lambda *a, **kw: subiu.append(True))
    monkeypatch.setattr(cli_mod, "_esperar_servidor", lambda *a, **kw: True)
    monkeypatch.setattr(cli_mod.webbrowser, "open", lambda url: None)

    r = roda(cli, "abrir")
    assert r.exit_code == 0
    assert subiu, "sem servidor no ar, ele tem que ser iniciado"


def test_abrir_avisa_quando_o_servidor_nao_sobe(cli, monkeypatch):
    monkeypatch.setattr(cli_mod, "_servidor_no_ar", lambda *a, **kw: False)
    monkeypatch.setattr(cli_mod, "_subir_servidor", lambda *a, **kw: None)
    monkeypatch.setattr(cli_mod, "_esperar_servidor", lambda *a, **kw: False)

    r = roda(cli, "abrir")
    assert r.exit_code != 0
    assert "transcrever serve" in r.output, "o erro tem que dizer onde ver o problema"


def test_abrir_ignora_arquivo_que_nao_e_midia(cli, tmp_path, monkeypatch):
    texto = tmp_path / "anotacoes.txt"
    texto.write_text("oi", encoding="utf-8")
    monkeypatch.setattr(cli_mod, "_servidor_no_ar", lambda *a, **kw: True)
    monkeypatch.setattr(cli_mod.webbrowser, "open", lambda url: None)

    r = roda(cli, "abrir", str(texto))
    assert r.exit_code == 0
    assert "não parece áudio/vídeo" in r.output


def test_abrir_cai_para_a_pasta_vigiada_se_a_api_falhar(cli, audio_file, home, monkeypatch):
    """Plano B: se a API não responder, a vigília ainda pega o arquivo."""
    monkeypatch.setattr(cli_mod, "_servidor_no_ar", lambda *a, **kw: True)
    monkeypatch.setattr(cli_mod, "_enfileirar_pela_api",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("api fora")))
    monkeypatch.setattr(cli_mod.webbrowser, "open", lambda url: None)

    r = roda(cli, "abrir", str(audio_file))
    assert r.exit_code == 0
    assert "pasta vigiada" in r.output
    assert (home / "var" / "inbox" / audio_file.name).exists()


# --------------------------------------------------------------------------
# Ícone no menu e "Abrir com"
# --------------------------------------------------------------------------

def test_desktop_instalar_cria_icone_e_abrir_com(cli, tmp_path, monkeypatch):
    dados = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(dados))

    r = roda(cli, "desktop", "instalar")
    assert r.exit_code == 0

    menu = dados / "applications" / "transcritor.desktop"
    abrir_com = dados / "applications" / "transcritor-arquivo.desktop"
    icone = dados / "icons" / "hicolor" / "scalable" / "apps" / "transcritor.svg"
    assert menu.exists() and abrir_com.exists() and icone.exists()

    texto = abrir_com.read_text(encoding="utf-8")
    assert "__EXEC__" not in texto and "__MIMES__" not in texto
    assert "abrir %F" in texto
    assert "Exec=" in texto and "/" in texto.split("Exec=")[1].split(" ")[0], \
        "o Exec precisa ser um caminho absoluto"
    assert "MimeType=" in texto and "audio/mpeg" in texto
    assert "NoDisplay=true" in texto

    assert "serve --open" in menu.read_text(encoding="utf-8")


def test_desktop_remover_apaga_o_que_criou(cli, tmp_path, monkeypatch):
    dados = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(dados))
    assert roda(cli, "desktop", "instalar").exit_code == 0

    r = roda(cli, "desktop", "remover")
    assert r.exit_code == 0
    assert not (dados / "applications" / "transcritor.desktop").exists()
    assert not (dados / "applications" / "transcritor-arquivo.desktop").exists()
    assert not (dados / "icons" / "hicolor" / "scalable" / "apps" / "transcritor.svg").exists()


def test_desktop_status_diz_o_que_falta(cli, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    r = roda(cli, "desktop", "status")
    assert r.exit_code == 0
    assert "transcritor.desktop" in r.output
    assert "não" in r.output

    roda(cli, "desktop", "instalar")
    r = roda(cli, "desktop", "status")
    assert "sim" in r.output


def test_mimetypes_cobrem_todos_os_formatos_de_entrada():
    tipos = cli_mod._mimetypes_suportados()
    for esperado in ("audio/mpeg", "audio/ogg", "audio/wav", "audio/flac",
                     "video/mp4", "video/webm", "video/x-matroska",
                     "video/quicktime", "video/mp2t"):
        assert esperado in tipos, f"faltou {esperado} no \"Abrir com\""
    # .ts seria adivinhado como uma linguagem do Qt; o mapa manual corrige.
    assert "text/vnd.trolltech.linguist" not in tipos


def test_mimetypes_saem_do_mesmo_lugar_que_a_vigilia():
    """Se uma extensão entra no programa, ela entra no \"Abrir com\" também."""
    import mimetypes

    tipos = set(cli_mod._mimetypes_suportados())
    for ext in config_mod.AUDIO_EXTENSIONS:
        tipo = cli_mod._MIMES_EXTRA.get(ext) or mimetypes.guess_type("x" + ext)[0]
        if tipo:
            assert tipo.lower() in tipos, f"{ext} ficou de fora do \"Abrir com\""


# --------------------------------------------------------------------------
# O que o instalador promete
# --------------------------------------------------------------------------

def test_instalador_existe_e_e_executavel():
    raiz = Path(__file__).resolve().parent.parent
    script = raiz / "instalar.sh"
    assert script.exists(), "o caminho de uma linha precisa do instalar.sh"
    assert script.stat().st_mode & 0o111, "instalar.sh precisa ser executável"


def test_instalador_nao_exige_decorar_comando():
    raiz = Path(__file__).resolve().parent.parent
    texto = (raiz / "instalar.sh").read_text(encoding="utf-8")
    assert "pipx install" in texto, "instalação tem que ser isolada e local"
    assert "desktop instalar" in texto, "o ícone faz parte da instalação"
    assert "doctor" in texto, "o instalador confere o resultado no fim"
    assert "TRANSCRITOR_PYTHON" not in texto, \
        "a instalação não pode exigir variável de ambiente"
