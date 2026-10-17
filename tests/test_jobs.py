"""Fila de jobs: estado, deduplicação, persistência e recuperação de queda."""

from __future__ import annotations

import json
import time
from pathlib import Path

from transcritor.jobs import Job, JobStore, new_id


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


# --------------------------------------------------------------------------
# Básico
# --------------------------------------------------------------------------

def test_new_id_curto_e_unico():
    ids = {new_id() for _ in range(200)}
    assert len(ids) == 200
    assert all(len(i) == 12 for i in ids)


def test_store_vazio(cfg):
    store = JobStore(cfg)
    assert store.all() == []
    assert store.counts()["total"] == 0
    assert store.next_queued() is None


def test_add_e_get(cfg, tmp_path):
    store = JobStore(cfg)
    src = touch(tmp_path / "audio.ogg")
    job = store.add(src, origin="cli")

    assert store.get(job.id).filename == "audio.ogg"
    assert job.status == "queued"
    assert job.origin == "cli"
    assert store.counts()["queued"] == 1


def test_add_arquivo_inexistente_ainda_enfileira(cfg, tmp_path):
    """O caso de erro é tratado no processamento, com mensagem clara."""
    store = JobStore(cfg)
    job = store.add(tmp_path / "fantasma.ogg")
    assert store.get(job.id) is not None


# --------------------------------------------------------------------------
# Deduplicação
# --------------------------------------------------------------------------

def test_nao_duplica_mesmo_arquivo(cfg, tmp_path):
    store = JobStore(cfg)
    src = touch(tmp_path / "a.ogg")
    j1 = store.add(src)
    j2 = store.add(src)
    assert j1.id == j2.id
    assert store.counts()["total"] == 1


def test_force_permite_reprocessar(cfg, tmp_path):
    store = JobStore(cfg)
    src = touch(tmp_path / "a.ogg")
    j1 = store.add(src)
    j2 = store.add(src, force=True)
    assert j1.id != j2.id
    assert store.counts()["total"] == 2


def test_find_by_source_usa_caminho_absoluto(cfg, tmp_path):
    store = JobStore(cfg)
    src = touch(tmp_path / "a.ogg")
    store.add(src)
    encontrado = store.find_by_source(str(src))
    assert encontrado is not None
    assert store.find_by_source(str(tmp_path / "outro.ogg")) is None


def test_erro_permite_reenfileirar(cfg, tmp_path):
    """Um job que falhou não pode bloquear uma nova tentativa automática."""
    store = JobStore(cfg)
    src = touch(tmp_path / "a.ogg")
    j1 = store.add(src)
    store.update(j1.id, status="error", error="falhou")

    j2 = store.add(src)          # origem watch, sem force
    assert j2.id != j1.id, "job com erro não deve impedir novo enfileiramento"


# --------------------------------------------------------------------------
# Atualização
# --------------------------------------------------------------------------

def test_update_campos(cfg, tmp_path):
    store = JobStore(cfg)
    job = store.add(touch(tmp_path / "a.ogg"))
    store.update(job.id, status="done", words=42, language="pt")

    atual = store.get(job.id)
    assert atual.status == "done"
    assert atual.words == 42
    assert atual.language == "pt"


def test_update_ignora_campo_desconhecido(cfg, tmp_path):
    store = JobStore(cfg)
    job = store.add(touch(tmp_path / "a.ogg"))
    store.update(job.id, inexistente=1)   # não pode levantar
    assert store.get(job.id) is not None


def test_update_job_inexistente_devolve_none(cfg):
    assert JobStore(cfg).update("naoexiste", status="done") is None


def test_set_progress(cfg, tmp_path):
    store = JobStore(cfg)
    job = store.add(touch(tmp_path / "a.ogg"))
    store.set_progress(job.id, {"phase": "transcribing", "percent": 42.5})
    assert store.get(job.id).progress["percent"] == 42.5


def test_run_seconds_calculado(cfg, tmp_path):
    store = JobStore(cfg)
    job = store.add(touch(tmp_path / "a.ogg"))
    store.update(job.id, started=1000.0, finished=1007.5)
    assert store.get(job.id).to_dict()["run_seconds"] == 7.5


def test_is_active(cfg, tmp_path):
    store = JobStore(cfg)
    job = store.add(touch(tmp_path / "a.ogg"))
    assert store.get(job.id).is_active is True
    store.update(job.id, status="done")
    assert store.get(job.id).is_active is False


# --------------------------------------------------------------------------
# Consultas
# --------------------------------------------------------------------------

def test_filtro_por_status_e_busca(cfg, tmp_path):
    store = JobStore(cfg)
    a = store.add(touch(tmp_path / "reuniao.ogg"))
    b = store.add(touch(tmp_path / "aula.ogg"))
    store.update(a.id, status="done")
    store.update(b.id, status="error")

    assert [j.id for j in store.all(status="done")] == [a.id]
    assert [j.id for j in store.all(status="error")] == [b.id]
    assert [j.id for j in store.all(search="reuniao")] == [a.id]
    assert store.counts()["done"] == 1
    assert store.counts()["error"] == 1


def test_ordenacao_mais_recente_primeiro(cfg, tmp_path):
    store = JobStore(cfg)
    antigo = store.add(touch(tmp_path / "1.ogg"))
    store.update(antigo.id, created=time.time() - 100)
    novo = store.add(touch(tmp_path / "2.ogg"))

    assert store.all()[0].id == novo.id


def test_limit(cfg, tmp_path):
    store = JobStore(cfg)
    for i in range(5):
        store.add(touch(tmp_path / f"{i}.ogg"))
    assert len(store.all(limit=2)) == 2


def test_next_queued_respeita_ordem(cfg, tmp_path):
    store = JobStore(cfg)
    primeiro = store.add(touch(tmp_path / "1.ogg"))
    store.add(touch(tmp_path / "2.ogg"))
    assert store.next_queued().id == primeiro.id


def test_next_queued_pula_finalizados(cfg, tmp_path):
    store = JobStore(cfg)
    a = store.add(touch(tmp_path / "1.ogg"))
    b = store.add(touch(tmp_path / "2.ogg"))
    store.update(a.id, status="done")
    assert store.next_queued().id == b.id


# --------------------------------------------------------------------------
# Remoção
# --------------------------------------------------------------------------

def test_remove(cfg, tmp_path):
    store = JobStore(cfg)
    job = store.add(touch(tmp_path / "a.ogg"))
    assert store.remove(job.id) is True
    assert store.get(job.id) is None
    assert store.remove(job.id) is False


def test_clear_finished_preserva_ativos(cfg, tmp_path):
    store = JobStore(cfg)
    pronto = store.add(touch(tmp_path / "1.ogg"))
    na_fila = store.add(touch(tmp_path / "2.ogg"))
    store.update(pronto.id, status="done")

    assert store.clear_finished() == 1
    assert store.get(pronto.id) is None
    assert store.get(na_fila.id) is not None


def test_requeue_failed(cfg, tmp_path):
    store = JobStore(cfg)
    a = store.add(touch(tmp_path / "1.ogg"))
    b = store.add(touch(tmp_path / "2.ogg"))
    store.update(a.id, status="error", error="quebrou", started=1.0, finished=2.0)
    store.update(b.id, status="done")

    assert store.requeue_failed() == 1
    assert store.get(a.id).status == "queued"
    assert store.get(a.id).error == ""
    assert store.get(a.id).started is None
    assert store.get(b.id).status == "done"


def test_requeue_failed_sem_erros(cfg, tmp_path):
    store = JobStore(cfg)
    store.add(touch(tmp_path / "1.ogg"))
    assert store.requeue_failed() == 0


# --------------------------------------------------------------------------
# Persistência
# --------------------------------------------------------------------------

def test_persiste_entre_instancias(cfg, tmp_path):
    store = JobStore(cfg)
    job = store.add(touch(tmp_path / "a.ogg"))
    store.update(job.id, status="done", words=10)

    outro = JobStore(cfg)
    recuperado = outro.get(job.id)
    assert recuperado is not None
    assert recuperado.status == "done"
    assert recuperado.words == 10


def test_estado_em_json_legivel(cfg, tmp_path):
    store = JobStore(cfg)
    store.add(touch(tmp_path / "a.ogg"))

    dados = json.loads(Path(cfg.state_file).read_text(encoding="utf-8"))
    assert dados["version"] == 1
    assert len(dados["jobs"]) == 1
    assert dados["jobs"][0]["filename"] == "a.ogg"


def test_recuperacao_apos_queda(cfg, tmp_path):
    """Regressão: um job 'running' quando o processo morre não pode ficar preso."""
    store = JobStore(cfg)
    job = store.add(touch(tmp_path / "a.ogg"))
    store.update(job.id, status="running", started=time.time())

    # simula reinício do programa
    novo = JobStore(cfg)
    recuperado = novo.get(job.id)
    assert recuperado.status == "error"
    assert "interrompido" in recuperado.error


def test_estado_corrompido_nao_quebra(cfg):
    Path(cfg.state_file).write_text("{{{ não é json", encoding="utf-8")
    assert JobStore(cfg).all() == []


def test_estado_parcialmente_invalido_e_tolerado(cfg):
    Path(cfg.state_file).write_text(json.dumps({
        "version": 1,
        "jobs": [{"id": "abc", "source": "/x", "filename": "x.ogg", "campo_novo": 1}],
    }), encoding="utf-8")
    store = JobStore(cfg)
    assert len(store.all()) == 1
    assert store.get("abc").filename == "x.ogg"


def test_save_e_atomico(cfg, tmp_path):
    """A escrita usa arquivo temporário + rename, nunca escrita parcial."""
    store = JobStore(cfg)
    store.add(touch(tmp_path / "a.ogg"))
    assert not Path(str(cfg.state_file) + ".tmp").exists()


def test_job_from_dict_ignora_extras():
    job = Job.from_dict({
        "id": "x", "source": "/s", "filename": "f.ogg",
        "is_active": True, "run_seconds": 3.0, "campo_futuro": "?",
    })
    assert job.id == "x"
    assert not hasattr(job, "campo_futuro")
