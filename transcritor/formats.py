"""Renderizadores de saída: txt, srt, vtt, json, md.

O TXT é o formato principal para leitura: os segmentos do Whisper são
reagrupados em parágrafos coerentes (pausas longas, fim de frase e limite
de caracteres), preservando a pontuação gerada pelo modelo.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from .engine import Segment, TranscriptionResult

# --------------------------------------------------------------------------
# Tempo
# --------------------------------------------------------------------------

def ts_srt(seconds: float) -> str:
    """00:00:01,234"""
    if seconds < 0:
        seconds = 0.0
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ts_vtt(seconds: float) -> str:
    """00:00:01.234"""
    return ts_srt(seconds).replace(",", ".")


def ts_clock(seconds: float) -> str:
    """1:02:03 ou 02:03"""
    seconds = max(0.0, seconds)
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def ts_human(seconds: float) -> str:
    """1h 02min 03s"""
    seconds = max(0.0, seconds)
    total = round(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h}h")
    if m or h:
        parts.append(f"{m:02d}min")
    parts.append(f"{s:02d}s")
    return " ".join(parts)


# --------------------------------------------------------------------------
# Agrupamento em parágrafos (para TXT/MD)
# --------------------------------------------------------------------------

_SENTENCE_END = (".", "!", "?", "…", ":", '"', "'", "”", "»", ")", "]")


def group_paragraphs(
    segments: Sequence[Segment],
    *,
    gap: float = 1.6,
    max_chars: int = 0,
    max_seconds: float = 45.0,
) -> list[dict[str, Any]]:
    """Junta segmentos em parágrafos.

    Regras de quebra, em ordem:
      1. pausa entre segmentos >= ``gap``;
      2. o segmento anterior termina uma frase E já passou de ~60% de max_seconds;
      3. o parágrafo acumulado passou de ``max_chars`` (se max_chars > 0);
      4. o parágrafo acumulado passou de ``max_seconds``.
    """
    paragraphs: list[dict[str, Any]] = []
    cur: list[Segment] = []

    def flush() -> None:
        nonlocal cur
        if not cur:
            return
        text = ""
        for seg in cur:
            t = seg.text.strip()
            if not t:
                continue
            if not text:
                text = t
            elif text.endswith(("-", "—")):
                text = text[:-1].rstrip() + t
            else:
                text += " " + t
        paragraphs.append({
            "start": cur[0].start,
            "end": cur[-1].end,
            "text": text.strip(),
            "segments": [s.id for s in cur],
        })
        cur = []

    prev: Segment | None = None
    for seg in segments:
        if not seg.text.strip():
            continue
        if prev is not None:
            gap_now = seg.start - prev.end
            dur = (cur[-1].end - cur[0].start) if cur else 0.0
            ends_sentence = prev.text.rstrip().endswith(_SENTENCE_END)
            cur_len = sum(len(s.text) for s in cur) + len(cur)
            broke = (
                gap_now >= gap
                or (ends_sentence and dur >= max_seconds * 0.6)
                or (max_chars and cur_len >= max_chars)
                or dur >= max_seconds
            )
            if broke:
                flush()
        cur.append(seg)
        prev = seg

    flush()
    return paragraphs


# --------------------------------------------------------------------------
# Formatos
# --------------------------------------------------------------------------

def render_txt(result: TranscriptionResult, cfg, *, timestamps: bool | None = None,
               header: bool = True) -> str:
    ts = cfg.txt_timestamps if timestamps is None else timestamps
    paras = group_paragraphs(
        result.segments,
        gap=cfg.txt_paragraph_gap,
        max_chars=cfg.txt_max_chars_per_line,
    )
    lines: list[str] = []
    if header:
        lines.append(f"# {_title(result)}")
        lines.append("")
    for p in paras:
        prefix = f"[{ts_clock(p['start'])}] " if ts else ""
        lines.append(prefix + _wrap(p["text"], cfg.txt_max_chars_per_line))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_srt(result: TranscriptionResult, cfg=None, *, max_chars: int = 42,
               max_line_seconds: float = 6.0) -> str:
    cues = _cues(result.segments, max_chars=max_chars, max_line_seconds=max_line_seconds)
    out: list[str] = []
    for i, (start, end, text) in enumerate(cues, 1):
        out.append(str(i))
        out.append(f"{ts_srt(start)} --> {ts_srt(end)}")
        out.append(text)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_vtt(result: TranscriptionResult, cfg=None, *, max_chars: int = 42,
               max_line_seconds: float = 6.0) -> str:
    cues = _cues(result.segments, max_chars=max_chars, max_line_seconds=max_line_seconds)
    out = ["WEBVTT", ""]
    for i, (start, end, text) in enumerate(cues, 1):
        out.append(str(i))
        out.append(f"{ts_vtt(start)} --> {ts_vtt(end)}")
        out.append(text)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_json(result: TranscriptionResult, cfg=None) -> str:
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n"


def render_md(result: TranscriptionResult, cfg) -> str:
    paras = group_paragraphs(
        result.segments,
        gap=cfg.txt_paragraph_gap,
        max_chars=cfg.txt_max_chars_per_line,
    )
    d = result.to_dict()
    lines = [
        f"# {_title(result)}",
        "",
        "## Metadados",
        "",
        "| Campo | Valor |",
        "| --- | --- |",
        f"| Arquivo | `{d['source']}` |",
        f"| Modelo | {d['model']} |",
        f"| Idioma | {d['language']} (confiança {d['language_probability']:.0%}) |",
        f"| Duração | {ts_human(d['duration'])} |",
        f"| Duração com fala | {ts_human(d['duration_after_vad'])} |",
        f"| Tempo de processamento | {ts_human(d['elapsed'])} |",
        f"| Velocidade | {d['speed']:.2f}x tempo real |",
        f"| Segmentos | {d['segment_count']} |",
        f"| Palavras | {len(d['text'].split())} |",
        "",
        "## Transcrição",
        "",
    ]
    for p in paras:
        lines.append(f"**[{ts_clock(p['start'])}]** {p['text']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


RENDERERS = {
    "txt": render_txt,
    "srt": render_srt,
    "vtt": render_vtt,
    "json": render_json,
    "md": render_md,
}


def render(fmt: str, result: TranscriptionResult, cfg) -> str:
    try:
        fn = RENDERERS[fmt]
    except KeyError:
        raise ValueError(f"formato desconhecido: {fmt!r} (use {', '.join(RENDERERS)})") from None
    if fmt == "json":
        return fn(result)
    return fn(result, cfg)


# --------------------------------------------------------------------------
# Internos
# --------------------------------------------------------------------------

def _title(result: TranscriptionResult) -> str:
    from pathlib import Path
    return Path(result.source).stem or "transcrição"


def _wrap(text: str, width: int) -> str:
    if not width:
        return text
    import textwrap
    return textwrap.fill(text, width=width, break_long_words=False, break_on_hyphens=False)


def _cues(segments: Sequence[Segment], *, max_chars: int, max_line_seconds: float
          ) -> list[tuple[float, float, str]]:
    """Quebra segmentos em legendas legíveis por caractere e por tempo."""
    cues: list[tuple[float, float, str]] = []
    for seg in segments:
        text = " ".join(seg.text.split())
        if not text:
            continue
        dur = max(0.001, seg.end - seg.start)

        # quebra por tempo: quantas cues cabem neste segmento
        n_by_time = max(1, int(dur / max_line_seconds + 0.999))
        # quebra por tamanho: quantas cues o texto exige
        n_by_len = max(1, int(len(text) / max_chars + 0.999))
        n = max(1, n_by_time, n_by_len)

        chunks = _split_text_even(text, n, max_chars)
        per = dur / len(chunks)
        for i, chunk in enumerate(chunks):
            start = seg.start + i * per
            end = start + per
            if i == len(chunks) - 1:
                end = seg.end
            cues.append((start, end, chunk))

    # remove sobreposições entre cues consecutivas
    for i in range(len(cues) - 1):
        s, e, t = cues[i]
        ns = cues[i + 1][0]
        if e > ns:
            cues[i] = (s, max(s + 0.05, ns - 0.001), t)
    return cues


def _split_text_even(text: str, n: int, max_chars: int) -> list[str]:
    """Divide o texto em até ``n`` partes equilibradas **por caracteres**,
    respeitando limites de palavra.

    O equilíbrio por caracteres (em vez de por número de palavras) evita
    legendas tortas, onde uma linha curta convive com outra muito longa.
    """
    words = text.split()
    if n <= 1 or len(words) <= 1:
        return [text]
    n = min(n, len(words))
    target = len(text) / n

    parts: list[str] = []
    cur: list[str] = []
    cur_len = 0
    parts_left = n

    for i, w in enumerate(words):
        cur.append(w)
        cur_len += len(w) + 1
        words_left = len(words) - i - 1
        remaining = parts_left - 1
        if remaining <= 0:
            continue
        # quebra obrigatória: precisa sobrar pelo menos uma palavra por parte
        must_break = words_left <= remaining
        want_break = cur_len >= target
        if must_break or want_break:
            parts.append(" ".join(cur))
            cur, cur_len = [], 0
            parts_left -= 1

    if cur:
        parts.append(" ".join(cur))
    return [p for p in parts if p] or [text]
