"""Mídia: detecção de extensão, ffprobe e normalização para 16 kHz mono."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from transcritor import media


def _tem_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


# --------------------------------------------------------------------------
# Extensões
# --------------------------------------------------------------------------

@pytest.mark.parametrize("nome,esperado", [
    ("audio.ogg", True),
    ("audio.OGG", True),
    ("WhatsApp Audio 2026-07-10 at 10.51.19.ogg", True),
    ("voz.opus", True),
    ("voz.oga", True),
    ("musica.mp3", True),
    ("reuniao.m4a", True),
    ("filme.mp4", True),
    ("filme.MKV", True),
    ("notas.txt", False),
    ("planilha.xlsx", False),
    ("arquivo", False),
    ("vazio.", False),
])
def test_is_media_file(nome, esperado):
    assert media.is_media_file(nome) is esperado


def test_is_media_file_aceita_path(tmp_path):
    assert media.is_media_file(tmp_path / "audio.flac") is True


# --------------------------------------------------------------------------
# ferramentas
# --------------------------------------------------------------------------

def test_ffmpeg_e_ffprobe_presentes():
    if not _tem_ffmpeg():
        pytest.skip("ffmpeg/ffprobe não instalados")
    assert media.ffmpeg_path()
    assert media.ffprobe_path()


# --------------------------------------------------------------------------
# probe
# --------------------------------------------------------------------------

def test_probe_arquivo_real(audio_file: Path):
    info = media.probe(audio_file)
    assert info.has_audio is True
    assert info.has_video is False
    assert info.sample_rate == 16000
    assert info.channels == 1
    assert info.duration == pytest.approx(3.0, abs=0.2)
    assert info.size_bytes > 0
    assert info.path == str(audio_file)


def test_probe_mp3(audio_file: Path, tmp_path: Path):
    """O caminho mais comum (WhatsApp/gravador) tem que funcionar."""
    mp3 = tmp_path / "convertido.mp3"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(audio_file),
         "-b:a", "64k", "-y", str(mp3)],
        check=True, capture_output=True,
    )
    info = media.probe(mp3)
    assert info.has_audio
    assert info.duration == pytest.approx(3.0, abs=0.3)


def test_probe_arquivo_inexistente_nao_levanta(tmp_path: Path):
    info = media.probe(tmp_path / "fantasma.ogg")
    assert info.has_audio is False
    assert info.duration == 0.0
    assert info.size_bytes == 0


def test_probe_arquivo_nao_audio_nao_levanta(tmp_path: Path):
    """Um .wav que não é áudio: ffprobe falha, mas nada explode."""
    falso = tmp_path / "falso.wav"
    falso.write_text("isto é texto, não é áudio", encoding="utf-8")
    info = media.probe(falso)
    assert info.has_audio is False
    assert info.has_video is False


def test_probe_diretorio_nao_levanta(tmp_path: Path):
    info = media.probe(tmp_path)
    assert info.has_audio is False


def test_probe_detecta_video_sem_audio(tmp_path: Path):
    if not _tem_ffmpeg():
        pytest.skip("ffmpeg não instalado")
    video = tmp_path / "silencio.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-pix_fmt", "yuv420p", "-y", str(video)],
        check=True, capture_output=True,
    )
    info = media.probe(video)
    assert info.has_video is True
    assert info.has_audio is False


def test_probe_video_com_audio(tmp_path: Path):
    if not _tem_ffmpeg():
        pytest.skip("ffmpeg não instalado")
    video = tmp_path / "com_audio.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-shortest", "-pix_fmt", "yuv420p", "-y", str(video)],
        check=True, capture_output=True,
    )
    info = media.probe(video)
    assert info.has_audio is True
    assert info.has_video is True


def test_media_info_to_dict(audio_file: Path):
    d = media.probe(audio_file).to_dict()
    assert set(d) == {"path", "duration", "has_audio", "has_video", "audio_codec",
                      "sample_rate", "channels", "size_bytes"}
    assert isinstance(d["duration"], float)


# --------------------------------------------------------------------------
# normalize
# --------------------------------------------------------------------------

def test_normalize_gera_wav_16k_mono(audio_file: Path, tmp_path: Path):
    destino = tmp_path / "saida" / "audio16k.wav"
    out = media.normalize(audio_file, destino)

    assert out == destino
    assert destino.exists()
    info = media.probe(destino)
    assert info.has_audio
    assert info.sample_rate == 16000
    assert info.channels == 1
    assert info.duration == pytest.approx(3.0, abs=0.2)


def test_normalize_cria_diretorio_pai(audio_file: Path, tmp_path: Path):
    destino = tmp_path / "a" / "b" / "c" / "audio.wav"
    media.normalize(audio_file, destino)
    assert destino.exists()


def test_normalize_converte_mp3(audio_file: Path, tmp_path: Path):
    mp3 = tmp_path / "entrada.mp3"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(audio_file),
         "-b:a", "64k", "-y", str(mp3)],
        check=True, capture_output=True,
    )
    destino = tmp_path / "saida.wav"
    media.normalize(mp3, destino)
    assert media.probe(destino).duration == pytest.approx(3.0, abs=0.3)


def test_normalize_com_loudnorm(audio_file: Path, tmp_path: Path):
    destino = tmp_path / "normalizado.wav"
    media.normalize(audio_file, destino, loudnorm=True)
    assert media.probe(destino).has_audio


def test_normalize_entrada_inexistente_levanta(tmp_path: Path):
    if not _tem_ffmpeg():
        pytest.skip("ffmpeg não instalado")
    with pytest.raises(subprocess.CalledProcessError):
        media.normalize(tmp_path / "nao_existe.ogg", tmp_path / "saida.wav")


def test_duration_atalho(audio_file: Path):
    assert media.duration(audio_file) == pytest.approx(3.0, abs=0.2)


def test_duration_de_arquivo_invalido_e_zero(tmp_path: Path):
    ruins = tmp_path / "ruim.ogg"
    ruins.write_bytes(b"nao e audio")
    assert media.duration(ruins) == 0.0


# --------------------------------------------------------------------------
# decode_pcm — o áudio que vai para o modelo
# --------------------------------------------------------------------------

def test_decode_pcm_entrega_float32_mono_16k(audio_file: Path):
    import numpy as np

    amostras = media.decode_pcm(audio_file)
    assert amostras.dtype == np.float32
    assert amostras.ndim == 1
    assert len(amostras) == pytest.approx(3 * 16000, rel=0.02)
    assert 0.0 < float(abs(amostras).max()) <= 1.0


def test_decode_pcm_bate_com_a_conversao_do_faster_whisper(audio_file: Path):
    """O contrato é o mesmo do faster-whisper: int16 dividido por 32768.

    Se esta conta mudar, a transcrição muda de resultado sem ninguém pedir.
    """
    import wave

    import numpy as np

    amostras = media.decode_pcm(audio_file)
    with wave.open(str(audio_file), "rb") as w:
        bruto = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")

    assert amostras.size == bruto.size
    assert np.array_equal(amostras, bruto.astype(np.float32) / 32768.0)


def test_decode_pcm_reescreve_para_16k(audio_file: Path, tmp_path: Path):
    """Entrada de 44,1 kHz tem que chegar em 16 kHz (o modelo exige)."""
    if not _tem_ffmpeg():
        pytest.skip("ffmpeg não instalado")

    quarenta_e_um = tmp_path / "44k.wav"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(audio_file),
         "-ar", "44100", "-ac", "2", "-y", str(quarenta_e_um)],
        check=True, capture_output=True,
    )
    assert media.probe(quarenta_e_um).sample_rate == 44100

    amostras = media.decode_pcm(quarenta_e_um)
    assert len(amostras) == pytest.approx(3 * 16000, rel=0.05)


def test_decode_pcm_le_formato_comprimido(audio_file: Path, tmp_path: Path):
    """O caso real (mensagem de voz, gravador) é mp3/ogg, não wav."""
    mp3 = tmp_path / "entrada.mp3"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(audio_file),
         "-b:a", "64k", "-y", str(mp3)],
        check=True, capture_output=True,
    )
    amostras = media.decode_pcm(mp3)
    assert amostras.dtype.name == "float32"
    assert len(amostras) == pytest.approx(3 * 16000, rel=0.1)


def test_decode_pcm_arquivo_invalido_explica_o_problema(tmp_path: Path):
    """A mensagem tem que dizer o que houve, não despejar a linha do ffmpeg."""
    if not _tem_ffmpeg():
        pytest.skip("ffmpeg não instalado")

    ruim = tmp_path / "ruim.ogg"
    ruim.write_bytes(b"isto nao e audio")
    with pytest.raises(RuntimeError, match=r"não foi possível decodificar ruim\.ogg"):
        media.decode_pcm(ruim)
