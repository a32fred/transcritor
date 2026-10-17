"""Motor: estado dos modelos e o download medido (sem tocar na rede).

O download real busca de 1,5 a 3 GB no Hugging Face. Aqui o `snapshot_download`
é substituído, mas o que se testa é justamente o comportamento que importa para
o usuário: o progresso é anunciado, medido e termina em 100%.
"""

from __future__ import annotations

from pathlib import Path

import huggingface_hub
import pytest

import transcritor.engine as engine_mod


@pytest.fixture
def snapshot_falso(monkeypatch):
    """Substitui o download do Hugging Face por um que só cria arquivos."""
    chamadas: list[dict] = []

    def fake(repo_id, local_dir=None, allow_patterns=None, **kw):
        chamadas.append({"repo": repo_id, "local": str(local_dir), "kw": kw})
        alvo = Path(local_dir)
        alvo.mkdir(parents=True, exist_ok=True)
        (alvo / "model.bin").write_bytes(b"x" * (2 * 1024 * 1024))
        (alvo / "config.json").write_text("{}", encoding="utf-8")
        return str(alvo)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake)
    return chamadas


def test_download_model_sem_progresso_apenas_baixa(cfg, snapshot_falso):
    caminho = engine_mod.download_model("tiny", cfg)
    assert Path(caminho, "model.bin").exists()
    assert snapshot_falso and snapshot_falso[0]["repo"] == "Systran/faster-whisper-tiny"


def test_download_model_mede_o_progresso(cfg, snapshot_falso):
    eventos: list[dict] = []
    caminho = engine_mod.download_model("tiny", cfg, eventos.append)

    fases = [e["phase"] for e in eventos]
    assert fases[0] == "downloading", "o download precisa ser anunciado antes"
    assert fases[-1] == "downloaded"
    assert eventos[-1]["percent"] == 100.0
    assert eventos[-1]["downloaded_mb"] > 0
    assert eventos[-1]["expected_mb"] == 75.0, "tamanho previsto do tiny"
    assert Path(caminho, "model.bin").exists()


def test_download_model_silencia_a_barra_do_huggingface(cfg, snapshot_falso):
    """Quem mostra o progresso é o transcritor: a barra de terceiro vira ruído."""
    engine_mod.download_model("tiny", cfg, lambda info: None)
    classe = snapshot_falso[0]["kw"].get("tqdm_class")
    assert classe is not None, "sem tqdm_class o huggingface imprime a barra dele"

    barra = classe(total=10)
    assert barra.disable is True


def test_download_model_propaga_o_erro(cfg, monkeypatch):
    def falha(*a, **kw):
        raise OSError("rede caiu")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", falha)
    with pytest.raises(OSError, match="rede caiu"):
        engine_mod.download_model("tiny", cfg, lambda info: None)


def test_download_model_repo_arbitrario(cfg, snapshot_falso):
    caminho = engine_mod.download_model("Fulano/faster-whisper-custom", cfg)
    assert caminho.endswith("faster-whisper-custom")
    assert snapshot_falso[0]["repo"] == "Fulano/faster-whisper-custom"


def test_model_status_marca_baixado_e_ativo(cfg):
    baixado = cfg.models_dir / "faster-whisper-tiny"
    baixado.mkdir(parents=True)
    (baixado / "model.bin").write_bytes(b"x" * (2 * 1024 * 1024))

    por_nome = {m["name"]: m for m in engine_mod.model_status(cfg)}
    assert por_nome["tiny"]["downloaded"] is True
    assert por_nome["tiny"]["active"] is True
    assert por_nome["tiny"]["disk_mb"] > 0
    assert por_nome["large-v3"]["downloaded"] is False
    assert por_nome["large-v3"]["active"] is False
    assert por_nome["large-v3"]["approx_mb"] == 3090


def test_model_status_marca_ativo_o_modelo_da_config(cfg):
    cfg.model = "small"
    por_nome = {m["name"]: m for m in engine_mod.model_status(cfg)}
    assert por_nome["small"]["active"] is True
    assert por_nome["tiny"]["active"] is False
