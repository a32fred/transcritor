"""Pipeline: diretórios de saída, artefatos e mensagens de erro."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from transcritor.pipeline import Pipeline


@pytest.fixture
def pipeline(cfg, engine) -> Pipeline:
    return Pipeline(cfg, engine)


def _faz_video_sem_audio(destino: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-pix_fmt", "yuv420p", "-y", str(destino)],
        check=True, capture_output=True,
    )


# --------------------------------------------------------------------------
# outdir_for
# --------------------------------------------------------------------------

def test_outdir_usa_o_nome_do_arquivo(cfg, pipeline, tmp_path):
    src = tmp_path / "reuniao do time.mp3"
    src.write_bytes(b"x")
    assert pipeline.outdir_for(src) == cfg.outbox / "reuniao do time"


def test_outdir_e_estavel_para_o_mesmo_arquivo(cfg, pipeline, tmp_path):
    src = tmp_path / "aula.mp3"
    src.write_bytes(b"x")

    d = pipeline.outdir_for(src)
    d.mkdir(parents=True)
    (d / ".meta.json").write_text(json.dumps({"source": str(src)}), encoding="utf-8")

    assert pipeline.outdir_for(src) == d, "reprocessar o mesmo arquivo não pode criar pasta nova"


def test_outdir_desambigua_com_a_extensao(cfg, pipeline, tmp_path):
    a = tmp_path / "x" / "aula.mp3"
    b = tmp_path / "y" / "aula.ogg"
    a.parent.mkdir(parents=True)
    b.parent.mkdir(parents=True)
    a.write_bytes(b"1")
    b.write_bytes(b"2")

    d1 = pipeline.outdir_for(a)
    assert d1 == cfg.outbox / "aula"
    d1.mkdir(parents=True)
    (d1 / ".meta.json").write_text(json.dumps({"source": str(a)}), encoding="utf-8")

    d2 = pipeline.outdir_for(b)
    assert d2 == cfg.outbox / "aula-ogg"


def test_outdir_numera_quando_a_extensao_repete(cfg, pipeline, tmp_path):
    def prepara(sub: str) -> Path:
        p = tmp_path / sub / "aula.mp3"
        p.parent.mkdir(parents=True)
        p.write_bytes(b"x")
        return p

    a, b, c = prepara("1"), prepara("2"), prepara("3")

    d1 = pipeline.outdir_for(a)
    d1.mkdir(parents=True)
    (d1 / ".meta.json").write_text(json.dumps({"source": str(a)}), encoding="utf-8")

    d2 = pipeline.outdir_for(b)
    assert d2 == cfg.outbox / "aula-mp3"
    d2.mkdir(parents=True)
    (d2 / ".meta.json").write_text(json.dumps({"source": str(b)}), encoding="utf-8")

    d3 = pipeline.outdir_for(c)
    assert d3 == cfg.outbox / "aula-mp3-2"


def test_outdir_sanitiza_caracteres_invalidos(cfg, pipeline, tmp_path):
    src = tmp_path / "áudio: reunião?.ogg"
    src.write_bytes(b"x")
    nome = pipeline.outdir_for(src).name
    assert ":" not in nome and "?" not in nome
    assert pipeline.outdir_for(src).parent == cfg.outbox


def test_outdir_pasta_existente_sem_meta_nao_quebra(cfg, pipeline, tmp_path):
    """Pasta criada à mão (sem .meta.json) não pode ser tratada como nossa."""
    src = tmp_path / "aula.ogg"
    src.write_bytes(b"x")
    (cfg.outbox / "aula").mkdir(parents=True, exist_ok=True)

    d = pipeline.outdir_for(src)
    assert d == cfg.outbox / "aula-ogg"


def test_outdir_meta_corrompido_nao_quebra(cfg, pipeline, tmp_path):
    src = tmp_path / "aula.ogg"
    src.write_bytes(b"x")
    base = cfg.outbox / "aula"
    base.mkdir(parents=True)
    (base / ".meta.json").write_text("{ não é json", encoding="utf-8")

    assert pipeline.outdir_for(src) == cfg.outbox / "aula-ogg"


def test_outdir_stem_vazio_usa_fallback(cfg, pipeline, tmp_path):
    src = tmp_path / "  .ogg"
    src.write_bytes(b"x")
    assert pipeline.outdir_for(src).parent == cfg.outbox


# --------------------------------------------------------------------------
# write_outputs
# --------------------------------------------------------------------------

def test_write_outputs_gera_todos_os_formatos(pipeline, result, tmp_path):
    out = tmp_path / "saida"
    written = pipeline.write_outputs(result, out, ("txt", "srt", "vtt", "json", "md"))

    for f in ("txt", "srt", "vtt", "json", "md"):
        assert f in written
        assert Path(written[f]).exists()
        assert Path(written[f]).read_text(encoding="utf-8").strip()
    assert written["meta"] == str(out / ".meta.json")


def test_write_outputs_ignora_formato_desconhecido(pipeline, result, tmp_path):
    written = pipeline.write_outputs(result, tmp_path / "o", ("txt", "docx"))
    assert "txt" in written
    assert "docx" not in written


def test_write_outputs_meta_tem_o_essencial(pipeline, result, tmp_path):
    out = tmp_path / "o"
    pipeline.write_outputs(result, out, ("txt",), media=None)
    meta = json.loads((out / ".meta.json").read_text(encoding="utf-8"))

    assert meta["source"] == "/tmp/exemplo.ogg"
    assert meta["filename"] == "exemplo.ogg"
    assert meta["model"] == "fake-model"
    assert meta["language"] == "pt"
    assert meta["segments"] == 4
    assert meta["words"] > 0
    assert meta["formats"] == ["txt"]


def test_write_outputs_cria_diretorio(pipeline, result, tmp_path):
    destino = tmp_path / "a" / "b" / "c"
    pipeline.write_outputs(result, destino, ("txt",))
    assert destino.is_dir()


# --------------------------------------------------------------------------
# transcribe_file — erros claros
# --------------------------------------------------------------------------

def test_arquivo_inexistente(pipeline, tmp_path):
    with pytest.raises(FileNotFoundError, match="não encontrado"):
        pipeline.transcribe_file(tmp_path / "nao_existe.ogg")


def test_arquivo_sem_audio_tem_mensagem_clara(pipeline, tmp_path):
    falso = tmp_path / "texto.wav"
    falso.write_text("isto é texto puro", encoding="utf-8")

    with pytest.raises(ValueError, match="não há trilha de áudio reconhecível"):
        pipeline.transcribe_file(falso, job_id="j")


def test_arquivo_vazio_tem_mensagem_clara(pipeline, tmp_path):
    vazio = tmp_path / "vazio.ogg"
    vazio.write_bytes(b"")

    with pytest.raises(ValueError, match="não há trilha de áudio"):
        pipeline.transcribe_file(vazio, job_id="j")


def test_video_sem_audio_tem_mensagem_propria(pipeline, tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg não instalado")
    video = tmp_path / "video.mp4"
    _faz_video_sem_audio(video)

    with pytest.raises(ValueError, match="nenhuma trilha de áudio"):
        pipeline.transcribe_file(video, job_id="j")


# --------------------------------------------------------------------------
# transcribe_file — caminho feliz
# --------------------------------------------------------------------------

def test_transcricao_ponta_a_ponta(cfg, pipeline, engine, audio_file):
    out = pipeline.transcribe_file(audio_file, job_id="job1")

    assert out["result"].source == str(audio_file)
    assert Path(out["outdir"]).is_dir()
    for f in ("txt", "srt", "vtt", "json", "md", "meta"):
        assert f in out["outputs"], f"faltou {f}"
    assert not (cfg.work / "job1").exists(), "temporários devem ser apagados"


def test_resultado_tem_o_texto_do_motor(pipeline, audio_file):
    out = pipeline.transcribe_file(audio_file, job_id="j")
    texto = Path(out["outputs"]["txt"]).read_text(encoding="utf-8")
    assert "Bom dia!" in texto
    assert out["result"].language == "pt"


def test_ordem_das_etapas(pipeline, audio_file):
    visto: list[str] = []
    pipeline.transcribe_file(audio_file, job_id="j", stage=lambda p, e: visto.append(p))

    assert visto[0] == "probing"
    assert visto[-1] == "done"
    for a, b in (("probed", "normalizing"), ("normalizing", "transcribed"),
                 ("transcribed", "writing"), ("writing", "done")):
        assert visto.index(a) < visto.index(b), f"{a} deveria vir antes de {b}"
    assert "progress" in visto


def test_progresso_traz_percentual(pipeline, audio_file):
    eventos: list[dict] = []

    def stage(phase: str, extra: dict) -> None:
        if phase == "progress":
            eventos.append(extra)

    pipeline.transcribe_file(audio_file, job_id="j", stage=stage)
    assert eventos
    assert all("percent" in e for e in eventos)
    assert eventos[-1]["percent"] > eventos[0]["percent"]


def test_preprocessamento_entrega_wav_ao_motor(pipeline, engine, audio_file):
    pipeline.transcribe_file(audio_file, job_id="j")
    assert engine.calls
    assert engine.calls[0].endswith("audio16k.wav")


def test_sem_preprocessamento_entrega_original(pipeline, engine, audio_file):
    pipeline.transcribe_file(audio_file, job_id="j", preprocess=False)
    assert engine.calls == [str(audio_file)]


def test_formatos_escolhidos(pipeline, audio_file):
    out = pipeline.transcribe_file(audio_file, job_id="j", formats=("txt",))
    assert set(out["outputs"]) == {"txt", "meta"}


def test_outdir_explicito(pipeline, audio_file, tmp_path):
    destino = tmp_path / "minha_pasta"
    out = pipeline.transcribe_file(audio_file, job_id="j", outdir=destino)

    assert Path(out["outdir"]) == destino
    assert (destino / "transcricao.txt").exists()


def test_loudnorm_nao_quebra(cfg, pipeline, audio_file):
    """Mesmo se o filtro falhar, o pipeline cai para o original e conclui."""
    out = pipeline.transcribe_file(audio_file, job_id="j", loudnorm=True)
    assert Path(out["outputs"]["txt"]).exists()


def test_cancelamento_interrompe(pipeline, audio_file):
    ev = threading.Event()
    ev.set()
    with pytest.raises(RuntimeError, match="cancelado"):
        pipeline.transcribe_file(audio_file, job_id="j", cancel=ev)


def test_erro_do_motor_sobe_para_quem_chamou(pipeline, engine, audio_file):
    engine.fail_with = RuntimeError("memória insuficiente")
    with pytest.raises(RuntimeError, match="memória insuficiente"):
        pipeline.transcribe_file(audio_file, job_id="j")
