"""Pasta vigiada: estabilização de arquivos, evitação de duplicatas e filtros."""

from __future__ import annotations

from pathlib import Path

import pytest

from transcritor.jobs import JobStore
from transcritor.watcher import Watcher


class RunnerFalso:
    """Só registra o que foi submetido — não há modelo nem threads aqui."""

    def __init__(self) -> None:
        self.submetidos: list[str] = []

    def submit(self, job_id: str) -> None:
        self.submetidos.append(job_id)


@pytest.fixture
def cenario(cfg):
    """Watcher com tempo de estabilização zerado, para os testes não dormirem."""
    cfg.watch_settle_seconds = 0.0
    store = JobStore(cfg)
    runner = RunnerFalso()
    return cfg, store, runner, Watcher(cfg, store, runner)


def escreve(pasta: Path, nome: str, conteudo: bytes = b"audio-falso") -> Path:
    p = pasta / nome
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(conteudo)
    return p


# --------------------------------------------------------------------------
# Ciclo básico
# --------------------------------------------------------------------------

def test_primeira_passada_apenas_registra(cenario):
    cfg, store, runner, w = cenario
    escreve(cfg.inbox, "a.ogg")

    assert w.scan_once() == 0, "o primeiro contato só anota tamanho e mtime"
    assert runner.submetidos == []


def test_segunda_passada_enfileira(cenario):
    cfg, store, runner, w = cenario
    escreve(cfg.inbox, "a.ogg")
    w.scan_once()

    assert w.scan_once() == 1
    assert len(runner.submetidos) == 1
    assert len(store.all()) == 1
    assert store.all()[0].origin == "watch"


def test_nao_enfileira_o_mesmo_arquivo_duas_vezes(cenario):
    cfg, store, runner, w = cenario
    escreve(cfg.inbox, "a.ogg")
    w.scan_once()
    w.scan_once()

    for _ in range(5):
        w.scan_once()

    assert len(store.all()) == 1
    assert len(runner.submetidos) == 1


def test_submit_false_cria_job_sem_enfileirar(cenario):
    cfg, store, runner, w = cenario
    escreve(cfg.inbox, "a.ogg")
    w.scan_once(submit=False)

    assert w.scan_once(submit=False) == 1
    assert len(store.all()) == 1
    assert runner.submetidos == [], "modo --once processa em série, sem fila"


def test_job_carrega_metadados_do_arquivo(cenario):
    cfg, store, runner, w = cenario
    escreve(cfg.inbox, "reuniao da equipe.ogg")
    w.scan_once()
    w.scan_once()

    job = store.all()[0]
    assert job.filename == "reuniao da equipe.ogg"
    assert job.status == "queued"
    assert Path(job.source).is_absolute()


# --------------------------------------------------------------------------
# Arquivo em cópia
# --------------------------------------------------------------------------

def test_arquivo_ainda_em_copia_nao_e_enfileirado(cenario):
    cfg, store, runner, w = cenario
    destino = escreve(cfg.inbox, "grande.ogg", b"a" * 10)

    w.scan_once()
    destino.write_bytes(b"a" * 5_000)     # download ainda em andamento
    w.scan_once()

    assert runner.submetidos == [], "não pode transcrever arquivo incompleto"

    w.scan_once()                          # agora estável
    assert len(runner.submetidos) == 1


def test_estabilizacao_exige_tempo_minimo(cfg):
    cfg.watch_settle_seconds = 5.0
    store = JobStore(cfg)
    runner = RunnerFalso()
    w = Watcher(cfg, store, runner)

    escreve(cfg.inbox, "a.ogg")
    w.scan_once()
    assert w.scan_once() == 0, "estável, mas ainda dentro do tempo de carência"
    assert runner.submetidos == []


# --------------------------------------------------------------------------
# Filtros
# --------------------------------------------------------------------------

@pytest.mark.parametrize("nome", [
    "arquivo.ogg.part",
    "arquivo.ogg.tmp",
    "arquivo.ogg.crdownload",
    "arquivo.ogg.download",
    "arquivo.ogg.partial",
    ".oculto.ogg",
    "~$temporario.ogg",
])
def test_arquivos_parciais_sao_ignorados(cenario, nome):
    cfg, store, runner, w = cenario
    escreve(cfg.inbox, nome)
    w.scan_once()
    w.scan_once()

    assert store.all() == []
    assert runner.submetidos == []


@pytest.mark.parametrize("nome", ["notas.txt", "planilha.csv", "imagem.png", "arquivo", "doc.pdf"])
def test_nao_audio_e_ignorado(cenario, nome):
    cfg, store, runner, w = cenario
    escreve(cfg.inbox, nome)
    w.scan_once()
    w.scan_once()

    assert store.all() == []


def test_video_ignorado_quando_configurado(cfg):
    cfg.watch_settle_seconds = 0.0
    cfg.watch_include_video = False
    store = JobStore(cfg)
    runner = RunnerFalso()
    w = Watcher(cfg, store, runner)

    escreve(cfg.inbox, "filme.mp4")
    escreve(cfg.inbox, "voz.ogg")
    w.scan_once()
    w.scan_once()

    assert [j.filename for j in store.all()] == ["voz.ogg"]


def test_extensao_maiuscula_e_aceita(cenario):
    cfg, store, runner, w = cenario
    escreve(cfg.inbox, "AUDIO.OGG")
    w.scan_once()
    w.scan_once()
    assert len(store.all()) == 1


# --------------------------------------------------------------------------
# Subpastas e limpeza
# --------------------------------------------------------------------------

def test_encontra_arquivos_em_subpastas(cenario):
    cfg, store, runner, w = cenario
    escreve(cfg.inbox / "2026" / "julho", "a.ogg")
    w.scan_once()
    w.scan_once()

    assert len(store.all()) == 1
    assert store.all()[0].filename == "a.ogg"


def test_varios_arquivos_de_uma_vez(cenario):
    cfg, store, runner, w = cenario
    for i in range(4):
        escreve(cfg.inbox, f"audio-{i}.ogg")
    w.scan_once()
    w.scan_once()

    assert len(store.all()) == 4
    assert len(runner.submetidos) == 4


def test_arquivo_removido_para_de_ser_observado(cenario):
    cfg, store, runner, w = cenario
    p = escreve(cfg.inbox, "efemero.ogg")
    w.scan_once()
    p.unlink()

    w.scan_once()
    assert w._seen == {}, "referência a arquivo inexistente deve ser descartada"
    assert store.all() == []


def test_inbox_inexistente_e_criada(cfg):
    cfg.watch_settle_seconds = 0.0
    for d in (cfg.inbox,):
        if d.exists():
            import shutil
            shutil.rmtree(d)

    w = Watcher(cfg, JobStore(cfg), RunnerFalso())
    assert w.scan_once() == 0
    assert cfg.inbox.is_dir()


def test_estatisticas_do_watcher(cenario):
    cfg, store, runner, w = cenario
    escreve(cfg.inbox, "a.ogg")
    escreve(cfg.inbox, "b.mp3")
    escreve(cfg.inbox, "nota.txt")

    w.scan_once()
    w.scan_once()

    assert w.stats["enqueued"] == 2
    assert w.stats["scanned"] > 0


def test_evento_de_enfileiramento(cenario):
    cfg, store, runner, w = cenario
    eventos: list[tuple[str, dict]] = []
    w.on_event = lambda e, d: eventos.append((e, d))

    escreve(cfg.inbox, "a.ogg")
    w.scan_once()
    w.scan_once()

    assert eventos and eventos[0][0] == "enqueued"
    assert eventos[0][1]["filename"] == "a.ogg"
