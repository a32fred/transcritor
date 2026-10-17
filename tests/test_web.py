"""Servidor web: rotas, upload, transcrição ao vivo, download e streaming."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

import transcritor.runner as runner_mod
from transcritor.web import server as srv_mod

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def web(cfg, monkeypatch, engine_factory):
    """Sobe o servidor real em porta efêmera, com o motor de transcrição dublado."""
    monkeypatch.setattr(runner_mod, "Engine", engine_factory)

    srv = srv_mod.TranscritorServer(("127.0.0.1", 0), srv_mod.Handler, cfg, watch=False)
    srv_mod.Handler.cfg = cfg
    srv_mod.Handler.store = srv.store
    srv_mod.Handler.runner = srv.runner
    srv_mod.Handler.watcher = srv.watcher
    srv_mod.Handler.bus = srv.bus

    srv.runner.start()
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()

    try:
        yield srv
    finally:
        srv.runner.stop()
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def base(web) -> str:
    host, port = web.server_address[:2]
    return f"http://{host}:{port}"


def pedido(method: str, url: str, data=None, headers: dict | None = None):
    """Requisição HTTP que devolve (status, corpo, cabeçalhos) sem levantar erro."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def espera_job(web, job_id: str, timeout: float = 40.0):
    limite = time.time() + timeout
    while time.time() < limite:
        job = web.store.get(job_id)
        if job and job.status in ("done", "error", "canceled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} não terminou em {timeout}s: {web.store.get(job_id)}")


# --------------------------------------------------------------------------
# Interface e estado
# --------------------------------------------------------------------------

def test_pagina_inicial(base, cfg):
    status, corpo, headers = pedido("GET", base + "/")
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    html = corpo.decode("utf-8")
    assert "<html" in html.lower()
    assert cfg.model in html


def test_status_geral(base, cfg):
    status, corpo, _ = pedido("GET", base + "/api/status")
    assert status == 200
    d = json.loads(corpo)

    assert d["model"] == cfg.model
    assert d["language"] == "pt"
    assert d["inbox"] == str(cfg.inbox)
    assert d["outbox"] == str(cfg.outbox)
    assert d["counts"]["total"] == 0
    assert isinstance(d["engine_loaded"], bool)
    assert d["watching"] is False, "servidor subiu com watch=False"
    assert d["uptime"] >= 0


def test_lista_de_modelos(base, cfg):
    status, corpo, _ = pedido("GET", base + "/api/models")
    assert status == 200
    d = json.loads(corpo)
    assert d["active"] == cfg.model
    nomes = [m["name"] for m in d["models"]]
    assert "large-v3" in nomes
    assert all("downloaded" in m for m in d["models"])


def test_lista_de_jobs_vazia(base):
    status, corpo, _ = pedido("GET", base + "/api/jobs")
    assert status == 200
    assert json.loads(corpo) == []


def test_rota_inexistente_devolve_404(base):
    status, corpo, _ = pedido("GET", base + "/api/nao-existe")
    assert status == 404
    assert "error" in json.loads(corpo)


def test_favicon_sem_conteudo(base):
    status, _, _ = pedido("GET", base + "/favicon.ico")
    assert status == 204


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------

def test_upload_extensao_invalida(base):
    status, corpo, _ = pedido("POST", base + "/api/upload?name=notas.txt", data=b"conteudo")
    assert status == 400
    assert "não suportada" in json.loads(corpo)["error"]


def test_upload_sem_nome(base):
    status, corpo, _ = pedido("POST", base + "/api/upload", data=b"x")
    assert status == 400
    assert "ausente" in json.loads(corpo)["error"]


def test_upload_sem_corpo(base):
    status, corpo, _ = pedido("POST", base + "/api/upload?name=a.wav", data=b"")
    assert status == 400
    assert "vazio" in json.loads(corpo)["error"]


def test_upload_grava_na_inbox(base, web, audio_file):
    dados = Path(audio_file).read_bytes()
    status, corpo, _ = pedido("POST", base + "/api/upload?name=gravacao.wav", data=dados)
    assert status == 201

    d = json.loads(corpo)
    assert d["filename"] == "gravacao.wav"
    assert d["bytes"] == len(dados)
    assert Path(d["path"]).read_bytes() == dados
    assert Path(d["path"]).parent == web.cfg.inbox


def test_upload_nao_sobrescreve_arquivo_existente(base, web, audio_file):
    dados = Path(audio_file).read_bytes()
    pedido("POST", base + "/api/upload?name=mesmo.wav", data=dados)
    status, corpo, _ = pedido("POST", base + "/api/upload?name=mesmo.wav", data=dados)

    assert status == 201
    assert json.loads(corpo)["filename"] == "mesmo-1.wav"


def test_upload_nome_com_caminho_e_achatado(base, web, audio_file):
    """`../../etc/passwd.ogg` não pode escapar da inbox."""
    status, corpo, _ = pedido(
        "POST", base + "/api/upload?name=" + urllib.parse.quote("../../malicioso.ogg"),
        data=Path(audio_file).read_bytes())
    assert status == 201

    destino = Path(json.loads(corpo)["path"])
    assert destino.parent == web.cfg.inbox
    assert ".." not in str(destino)


# --------------------------------------------------------------------------
# Transcrição de ponta a ponta pela web
# --------------------------------------------------------------------------

def test_fluxo_completo_upload_ate_download(base, web, audio_file):
    dados = Path(audio_file).read_bytes()
    status, corpo, _ = pedido("POST", base + "/api/upload?name=entrevista.wav", data=dados)
    assert status == 201
    job_id = json.loads(corpo)["job_id"]

    job = espera_job(web, job_id)
    assert job.status == "done", job.error
    assert job.origin == "web"
    assert job.words > 0
    assert "Bom dia!" in job.preview
    assert job.language == "pt"
    assert Path(job.outdir).is_dir()

    # texto
    status, corpo, headers = pedido("GET", f"{base}/api/transcript/{job_id}?format=txt")
    assert status == 200
    assert "Bom dia!" in corpo.decode("utf-8")

    # todos os formatos pedidos
    for fmt in ("txt", "srt", "vtt", "json", "md"):
        status, corpo, _ = pedido("GET", f"{base}/api/transcript/{job_id}?format={fmt}")
        assert status == 200, f"formato {fmt} indisponível"
        assert corpo.strip()

    # download com cabeçalho de anexo
    status, corpo, headers = pedido("GET", f"{base}/api/download/{job_id}/srt")
    assert status == 200
    assert "attachment" in headers["Content-Disposition"]
    assert "entrevista.srt" in headers["Content-Disposition"]


def test_transcricao_tem_acentos_e_pontuacao(base, web, audio_file):
    dados = Path(audio_file).read_bytes()
    _, corpo, _ = pedido("POST", base + "/api/upload?name=ptbr.wav", data=dados)
    job_id = json.loads(corpo)["job_id"]
    espera_job(web, job_id)

    _, corpo, _ = pedido("GET", f"{base}/api/transcript/{job_id}?format=txt")
    texto = corpo.decode("utf-8")
    assert "é" in texto or "ã" in texto
    assert "," in texto and "?" in texto


def test_json_da_transcricao_e_valido(base, web, audio_file):
    dados = Path(audio_file).read_bytes()
    _, corpo, _ = pedido("POST", base + "/api/upload?name=j.wav", data=dados)
    job_id = json.loads(corpo)["job_id"]
    espera_job(web, job_id)

    _, corpo, _ = pedido("GET", f"{base}/api/transcript/{job_id}?format=json")
    d = json.loads(corpo)
    assert d["language"] == "pt"
    assert d["segment_count"] == 4
    assert len(d["segments"]) == 4


def test_transcricao_de_job_pendente_devolve_estado(base, web, audio_file):
    """Enquanto não termina, /api/transcript devolve o estado em vez de erro."""
    job = web.store.add(audio_file, origin="web")   # criado, mas não submetido
    assert web.store.get(job.id).status == "queued"

    status, corpo, _ = pedido("GET", f"{base}/api/transcript/{job.id}?format=txt")
    assert status == 200
    d = json.loads(corpo)
    assert d["status"] == "queued"
    assert "progress" in d


def test_transcricao_de_job_inexistente(base):
    status, corpo, _ = pedido("GET", base + "/api/transcript/naoexiste?format=txt")
    assert status == 404


def test_download_de_formato_inexistente(base, web, audio_file):
    dados = Path(audio_file).read_bytes()
    _, corpo, _ = pedido("POST", base + "/api/upload?name=x.wav", data=dados)
    job_id = json.loads(corpo)["job_id"]
    espera_job(web, job_id)

    status, _, _ = pedido("GET", f"{base}/api/download/{job_id}/docx")
    assert status == 404


# --------------------------------------------------------------------------
# Streaming do áudio original
# --------------------------------------------------------------------------

def test_audio_original_e_servido(base, web, audio_file):
    dados = Path(audio_file).read_bytes()
    _, corpo, _ = pedido("POST", base + "/api/upload?name=player.wav", data=dados)
    job_id = json.loads(corpo)["job_id"]

    status, corpo, headers = pedido("GET", f"{base}/api/audio/{job_id}")
    assert status == 200
    assert corpo == dados
    assert headers["Accept-Ranges"] == "bytes"


def test_audio_aceita_range(base, web, audio_file):
    dados = Path(audio_file).read_bytes()
    _, corpo, _ = pedido("POST", base + "/api/upload?name=range.wav", data=dados)
    job_id = json.loads(corpo)["job_id"]

    status, corpo, headers = pedido(
        "GET", f"{base}/api/audio/{job_id}", headers={"Range": "bytes=0-99"})
    assert status == 206
    assert len(corpo) == 100
    assert corpo == dados[:100]
    assert headers["Content-Range"] == f"bytes 0-99/{len(dados)}"


def test_audio_range_invalido_devolve_416(base, web, audio_file):
    dados = Path(audio_file).read_bytes()
    _, corpo, _ = pedido("POST", base + "/api/upload?name=inv.wav", data=dados)
    job_id = json.loads(corpo)["job_id"]

    status, _, headers = pedido(
        "GET", f"{base}/api/audio/{job_id}", headers={"Range": f"bytes={len(dados) + 10}-"})
    assert status == 416
    assert "Content-Range" in headers


def test_audio_de_job_inexistente(base):
    status, _, _ = pedido("GET", base + "/api/audio/naoexiste")
    assert status == 404


# --------------------------------------------------------------------------
# Ações sobre jobs
# --------------------------------------------------------------------------

def test_adicionar_caminhos_locais(base, web, audio_file):
    corpo_pedido = json.dumps({"paths": [str(audio_file)]}).encode()
    status, corpo, headers = pedido("POST", base + "/api/jobs", data=corpo_pedido,
                                    headers={"Content-Type": "application/json"})
    assert status == 200
    d = json.loads(corpo)
    assert d["count"] == 1

    job_id = d["added"][0]["job_id"]
    assert espera_job(web, job_id).status == "done"


def test_adicionar_caminho_inexistente_e_ignorado(base, tmp_path):
    corpo_pedido = json.dumps({"paths": [str(tmp_path / "nao_existe.ogg")]}).encode()
    status, corpo, _ = pedido("POST", base + "/api/jobs", data=corpo_pedido,
                              headers={"Content-Type": "application/json"})
    assert status == 200
    assert json.loads(corpo)["count"] == 0


def test_adicionar_arquivo_nao_audio_e_ignorado(base, tmp_path):
    texto = tmp_path / "notas.txt"
    texto.write_text("oi", encoding="utf-8")

    corpo_pedido = json.dumps({"paths": [str(texto)]}).encode()
    _, corpo, _ = pedido("POST", base + "/api/jobs", data=corpo_pedido,
                         headers={"Content-Type": "application/json"})
    assert json.loads(corpo)["count"] == 0


def test_retry_de_job(base, web, audio_file):
    corpo_pedido = json.dumps({"paths": [str(audio_file)]}).encode()
    _, corpo, _ = pedido("POST", base + "/api/jobs", data=corpo_pedido,
                         headers={"Content-Type": "application/json"})
    job_id = json.loads(corpo)["added"][0]["job_id"]
    espera_job(web, job_id)

    status, corpo, _ = pedido("POST", base + f"/api/jobs/{job_id}/retry")
    assert status == 200
    assert json.loads(corpo)["requeued"] is True
    assert espera_job(web, job_id).status == "done"


def test_retry_de_job_inexistente(base):
    status, _, _ = pedido("POST", base + "/api/jobs/naoexiste/retry")
    assert status == 404


def test_cancelar_job_na_fila(base, web, audio_file):
    job = web.store.add(audio_file, origin="web")
    status, corpo, _ = pedido("POST", base + f"/api/jobs/{job.id}/cancel")
    assert status == 200
    assert json.loads(corpo)["canceled"] is True
    assert web.store.get(job.id).status == "canceled"


def test_cancelar_nao_mexe_em_job_ja_pronto(base, web, audio_file):
    corpo_pedido = json.dumps({"paths": [str(audio_file)]}).encode()
    _, corpo, _ = pedido("POST", base + "/api/jobs", data=corpo_pedido,
                         headers={"Content-Type": "application/json"})
    job_id = json.loads(corpo)["added"][0]["job_id"]
    espera_job(web, job_id)

    _, corpo, _ = pedido("POST", base + f"/api/jobs/{job_id}/cancel")
    assert json.loads(corpo)["canceled"] is False
    assert web.store.get(job_id).status == "done"


def test_remover_do_historico(base, web, audio_file):
    job = web.store.add(audio_file, origin="web")

    status, corpo, _ = pedido("DELETE", base + f"/api/jobs/{job.id}")
    assert status == 200
    assert json.loads(corpo)["removed"] is True
    assert web.store.get(job.id) is None

    status, corpo, _ = pedido("DELETE", base + f"/api/jobs/{job.id}")
    assert json.loads(corpo)["removed"] is False


def test_limpar_finalizados(base, web, audio_file, tmp_path):
    pronto = web.store.add(audio_file, origin="web")
    web.store.update(pronto.id, status="done")
    web.store.add(tmp_path / "na_fila.ogg", origin="web")

    status, corpo, _ = pedido("POST", base + "/api/jobs/clear")
    assert status == 200
    assert json.loads(corpo)["removed"] == 1
    assert web.store.get(pronto.id) is None
    assert web.store.counts()["total"] == 1


def test_scan_sem_watcher(base):
    """Com watch=False não há watcher, mas a rota não pode estourar."""
    status, corpo, _ = pedido("POST", base + "/api/scan")
    assert status == 200
    assert json.loads(corpo)["enqueued"] == 0


# --------------------------------------------------------------------------
# SSE
# --------------------------------------------------------------------------

def test_eventos_abre_stream(base):
    req = urllib.request.Request(base + "/api/events", method="GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        assert r.status == 200
        assert r.headers["Content-Type"].startswith("text/event-stream")
        primeira = r.readline()
    assert primeira == b": conectado\n"


def test_eventos_notifica_upload(base, web, audio_file):
    """Um consumidor do SSE recebe o evento de enfileiramento do upload."""
    recebido: list[bytes] = []

    def consome() -> None:
        req = urllib.request.Request(base + "/api/events", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                limite = time.time() + 25
                buffer = b""
                while time.time() < limite:
                    linha = r.readline()
                    if not linha:
                        break
                    buffer += linha
                    if b"evento.wav" in buffer:
                        recebido.append(buffer)
                        return
        except Exception:
            pass

    t = threading.Thread(target=consome, daemon=True)
    t.start()
    time.sleep(0.4)  # dá tempo de assinar antes do upload

    dados = Path(audio_file).read_bytes()
    pedido("POST", base + "/api/upload?name=evento.wav", data=dados)

    t.join(timeout=28)
    assert recebido, "nenhum evento de enfileiramento chegou pelo SSE"
    assert b"event: enqueued" in recebido[0]
    assert b"evento.wav" in recebido[0]


def test_eventos_reproduz_historico(base, web, audio_file):
    """Quem se conecta depois ainda vê os últimos eventos (replay)."""
    dados = Path(audio_file).read_bytes()
    pedido("POST", base + "/api/upload?name=historico.wav", data=dados)
    time.sleep(0.3)

    req = urllib.request.Request(base + "/api/events", method="GET")
    with urllib.request.urlopen(req, timeout=20) as r:
        buffer = b""
        limite = time.time() + 8
        while time.time() < limite and b"historico.wav" not in buffer:
            linha = r.readline()
            if not linha:
                break
            buffer += linha

    assert b"event: enqueued" in buffer, buffer
    assert b"historico.wav" in buffer
