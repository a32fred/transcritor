"""Formatos de saída: tempos, agrupamento em parágrafos e renderizadores."""

from __future__ import annotations

import json
import re

import pytest

from transcritor import formats as fmt
from transcritor.engine import Segment

# --------------------------------------------------------------------------
# Tempo
# --------------------------------------------------------------------------

@pytest.mark.parametrize("segundos,esperado", [
    (0, "00:00:00,000"),
    (1.234, "00:00:01,234"),
    (61.5, "00:01:01,500"),
    (3661.007, "01:01:01,007"),
    (-5, "00:00:00,000"),
])
def test_ts_srt(segundos, esperado):
    assert fmt.ts_srt(segundos) == esperado


def test_ts_vtt_usa_ponto():
    assert fmt.ts_vtt(1.234) == "00:00:01.234"
    assert "," not in fmt.ts_vtt(3661.5)


@pytest.mark.parametrize("segundos,esperado", [
    (0, "00:00"),
    (59, "00:59"),
    (60, "01:00"),
    (3599, "59:59"),
    (3600, "1:00:00"),
    (3723, "1:02:03"),
])
def test_ts_clock(segundos, esperado):
    assert fmt.ts_clock(segundos) == esperado


@pytest.mark.parametrize("segundos", [0, 1, 59, 60, 3599, 3600, 86399])
def test_ts_human(segundos):
    assert fmt.ts_human(segundos)


def test_ts_srt_sempre_crescente():
    """Timestamps não podem regredir — legenda que volta no tempo quebra players."""
    valores = [0, 0.001, 0.5, 1, 59.999, 60, 3600, 86400]
    textos = [fmt.ts_srt(v) for v in valores]
    assert textos == sorted(textos)


# --------------------------------------------------------------------------
# Agrupamento em parágrafos
# --------------------------------------------------------------------------

def seg(sid, start, end, text):
    return Segment(id=sid, start=start, end=end, text=text)


def test_paragrafo_unico_quando_tudo_colado():
    segs = [
        seg(0, 0.0, 2.0, "Primeira parte."),
        seg(1, 2.0, 4.0, "Segunda parte."),
    ]
    paras = fmt.group_paragraphs(segs, gap=1.6)
    assert len(paras) == 1
    assert paras[0]["text"] == "Primeira parte. Segunda parte."


def test_pausa_longa_cria_paragrafo():
    segs = [
        seg(0, 0.0, 2.0, "Antes da pausa."),
        seg(1, 5.0, 7.0, "Depois da pausa."),   # 3s de silêncio
    ]
    paras = fmt.group_paragraphs(segs, gap=1.6)
    assert len(paras) == 2
    assert paras[0]["text"] == "Antes da pausa."
    assert paras[1]["text"] == "Depois da pausa."


def test_paragrafo_registra_inicio_fim_e_segmentos():
    segs = [seg(0, 1.0, 2.0, "A."), seg(1, 2.0, 3.5, "B.")]
    p = fmt.group_paragraphs(segs)[0]
    assert p["start"] == 1.0
    assert p["end"] == 3.5
    assert p["segments"] == [0, 1]


def test_limite_de_tempo_forca_quebra():
    segs = [seg(i, i * 10.0, i * 10.0 + 9.9, f"Trecho {i}.") for i in range(20)]
    paras = fmt.group_paragraphs(segs, gap=99, max_seconds=30.0)
    assert len(paras) > 1
    for p in paras:
        assert p["end"] - p["start"] <= 45.0


def test_max_chars_forca_quebra():
    segs = [seg(i, i * 1.0, i * 1.0 + 0.9, "palavra " * 10) for i in range(5)]
    paras = fmt.group_paragraphs(segs, gap=99, max_chars=60, max_seconds=999)
    assert len(paras) > 1


def test_segmentos_vazios_sao_ignorados():
    segs = [seg(0, 0, 1, "  "), seg(1, 1, 2, "Texto."), seg(2, 2, 3, "")]
    paras = fmt.group_paragraphs(segs)
    assert len(paras) == 1
    assert paras[0]["text"] == "Texto."


def test_hifen_de_quebra_e_unido():
    """Palavra cortada com hífen no fim do segmento deve ser remendada."""
    segs = [seg(0, 0, 1, "trans-"), seg(1, 1, 2, "porte")]
    paras = fmt.group_paragraphs(segs, gap=99)
    assert "transporte" in paras[0]["text"]


def test_sem_segmentos_devolve_lista_vazia():
    assert fmt.group_paragraphs([]) == []


# --------------------------------------------------------------------------
# Renderizadores
# --------------------------------------------------------------------------

def test_txt_tem_cabecalho_e_paragrafos(result, cfg):
    out = fmt.render_txt(result, cfg)
    assert out.startswith("# exemplo")
    assert "Bom dia!" in out
    assert out.endswith("\n")


def test_txt_sem_cabecalho(result, cfg):
    out = fmt.render_txt(result, cfg, header=False)
    assert not out.startswith("#")


def test_txt_com_timestamps(result, cfg):
    out = fmt.render_txt(result, cfg, timestamps=True)
    assert re.search(r"^\[\d{2}:\d{2}\] ", out, re.M)


def test_txt_quebra_de_linha_quando_configurado(result, cfg):
    cfg.txt_max_chars_per_line = 40
    out = fmt.render_txt(result, cfg)
    corpo = [linha for linha in out.splitlines() if linha and not linha.startswith("#")]
    assert corpo
    assert all(len(linha) <= 40 for linha in corpo), [x for x in corpo if len(x) > 40]


def test_srt_estrutura_valida(result, cfg):
    out = fmt.render_srt(result, cfg)
    blocos = [b for b in out.strip().split("\n\n") if b.strip()]
    assert blocos
    for i, bloco in enumerate(blocos, 1):
        linhas = bloco.splitlines()
        assert linhas[0] == str(i), "numeração das cues tem que ser sequencial"
        assert re.fullmatch(r"\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}", linhas[1])
        assert len(linhas) >= 3, "cue sem texto"


def test_srt_nunca_sobrepoe_cues(result, cfg):
    """Cues podem se tocar (fim == início seguinte), mas nunca invadir."""
    out = fmt.render_srt(result, cfg)
    fins, inicios = [], []
    for bloco in out.strip().split("\n\n"):
        ini, fim = bloco.splitlines()[1].split(" --> ")
        inicios.append(ini)
        fins.append(fim)
    for fim, proximo in zip(fins, inicios[1:], strict=False):
        assert fim <= proximo, f"cue terminando em {fim} invade a seguinte ({proximo})"


def test_srt_respeita_duracao_do_audio(result, cfg):
    out = fmt.render_srt(result, cfg)
    ultimo = out.strip().split("\n\n")[-1].splitlines()[1].split(" --> ")[1]
    assert ultimo <= fmt.ts_srt(result.duration)


def test_vtt_comeca_com_webvtt(result, cfg):
    out = fmt.render_vtt(result, cfg)
    assert out.startswith("WEBVTT")
    assert "-->" in out
    assert re.search(r"\d{2}:\d{2}:\d{2}\.\d{3} --> \d{2}:\d{2}:\d{2}\.\d{3}", out)


def test_vtt_timestamps_usam_ponto_nao_virgula(result, cfg):
    """VTT usa ponto decimal — só os timecodes, o texto pode ter vírgulas."""
    out = fmt.render_vtt(result, cfg)
    timecodes = re.findall(r"\d{2}:\d{2}:\d{2}[.,]\d{3}", out)
    assert timecodes, "nenhum timecode encontrado"
    assert not any("," in tc for tc in timecodes), timecodes


def test_json_contem_metadados_e_segmentos(result, cfg):
    d = json.loads(fmt.render_json(result, cfg))
    assert d["language"] == "pt"
    assert d["segment_count"] == len(result.segments)
    assert d["text"].startswith("Bom dia!")
    assert len(d["segments"]) == len(result.segments)
    assert set(d["segments"][0]) >= {"id", "start", "end", "text"}


def test_md_tem_tabela_e_secoes(result, cfg):
    out = fmt.render_md(result, cfg)
    assert "## Metadados" in out
    assert "## Transcrição" in out
    assert "| Campo | Valor |" in out
    assert "faster" in out.lower() or "Modelo" in out


def test_render_despacha_todos_os_formatos(result, cfg):
    for f in ("txt", "srt", "vtt", "json", "md"):
        assert fmt.render(f, result, cfg)


def test_render_formato_invalido_levanta(result, cfg):
    with pytest.raises(ValueError, match="formato desconhecido"):
        fmt.render("docx", result, cfg)


# --------------------------------------------------------------------------
# Quebra de cues
# --------------------------------------------------------------------------

def test_cues_nunca_ficam_vazias(result):
    for _, _, texto in fmt._cues(result.segments, max_chars=42, max_line_seconds=6.0):
        assert texto.strip()


def test_cues_respeitam_limite_de_caracteres():
    segs = [seg(0, 0, 12, "palavra " * 30)]
    cues = fmt._cues(segs, max_chars=42, max_line_seconds=6.0)
    assert len(cues) >= 2
    # pode passar um pouco (2 linhas), mas não pode explodir
    assert all(len(t) <= 90 for _, _, t in cues)


def test_split_text_even_equilibra():
    texto = ("Validação final do Transcritor. Se esta frase aparecer com pontuação "
             "correta, o sistema está pronto para uso. Ponto final, vírgula e interrogação.")
    partes = fmt._split_text_even(texto, 4, 42)
    assert len(partes) == 4
    assert " ".join(partes) == texto, "não pode perder nem inventar palavras"
    tamanhos = [len(p) for p in partes]
    assert max(tamanhos) - min(tamanhos) < 25, f"muito desequilibrado: {tamanhos}"


@pytest.mark.parametrize("n", [1, 2, 3, 5, 12])
def test_split_text_even_preserva_conteudo(n):
    texto = "um dois três quatro cinco seis sete oito nove dez onze doze"
    partes = fmt._split_text_even(texto, n, 42)
    assert " ".join(partes) == texto


def test_split_text_even_casos_limite():
    assert fmt._split_text_even("Oi", 3, 42) == ["Oi"]
    assert fmt._split_text_even("", 3, 42) == [""]
    assert fmt._split_text_even("uma frase", 1, 42) == ["uma frase"]
