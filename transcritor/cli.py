"""CLI do transcritor (click)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

import click

from . import __version__
from .config import KNOWN_MODELS, Config
from .engine import Engine, download_model, model_status
from .formats import ts_human
from .jobs import JobStore
from .media import is_media_file, probe
from .pipeline import Pipeline
from .runner import Runner
from .watcher import Watcher

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(verbose: bool, quiet: bool = False) -> None:
    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt="%H:%M:%S")


def load_cfg(ctx: click.Context) -> Config:
    cfg = Config.load(ctx.obj.get("config") if ctx.obj else None)
    cfg.ensure_dirs()
    return cfg


# --------------------------------------------------------------------------
# Raiz
# --------------------------------------------------------------------------

@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--config", "config_path", type=click.Path(dir_okay=False, path_type=Path),
              default=None, help="Arquivo de configuração alternativo (JSON).")
@click.option("-v", "--verbose", is_flag=True, help="Log detalhado.")
@click.option("-q", "--quiet", is_flag=True, help="Só erros.")
@click.version_option(__version__, prog_name="transcritor")
@click.pass_context
def cli(ctx: click.Context, config_path: Path | None, verbose: bool, quiet: bool) -> None:
    """Transcritor de áudio local — faster-whisper em português do Brasil.

    \b
    Fluxo típico:
      transcritor transcribe audio.mp3      transcreve um arquivo
      transcritor watch                     vigia var/inbox e transcreve sozinho
      transcritor serve --open              abre a interface web
      transcritor tui                       painel no terminal
    """
    ctx.ensure_object(dict)
    ctx.obj["config"] = config_path
    ctx.obj["verbose"] = verbose
    setup_logging(verbose, quiet)


# --------------------------------------------------------------------------
# transcribe
# --------------------------------------------------------------------------

def _human_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"


def _bar(pct: float | None, width: int = 28) -> str:
    if pct is None:
        return "[" + "·" * width + "]"
    filled = int(width * max(0.0, min(100.0, pct)) / 100.0)
    return "[" + "█" * filled + "░" * (width - filled) + f"] {pct:5.1f}%"


@cli.command("transcribe", short_help="Transcreve arquivos de áudio/vídeo.")
@click.argument("paths", nargs=-1, required=True,
                type=click.Path(exists=True, dir_okay=True, path_type=Path))
@click.option("-o", "--out", "outdir", type=click.Path(file_okay=False, path_type=Path),
              default=None, help="Pasta de saída (padrão: var/outbox/<nome>).")
@click.option("-m", "--model", default=None, help=f"Modelo: {', '.join(KNOWN_MODELS)}.")
@click.option("-l", "--language", default=None, help="Idioma (pt, en, es…). Use 'auto' para detectar.")
@click.option("-t", "--task", default=None, type=click.Choice(["transcribe", "translate"]),
              help="transcribe mantém o idioma; translate traduz para inglês.")
@click.option("-f", "--format", "formats", multiple=True,
              type=click.Choice(["txt", "srt", "vtt", "json", "md"]),
              help="Formatos de saída (repetível). Padrão: todos os configurados.")
@click.option("--no-preprocess", is_flag=True,
              help="Não normalizar via ffmpeg (mais rápido, menos robusto).")
@click.option("--loudnorm", is_flag=True, help="Normalizar volume (áudio muito baixo).")
@click.option("--word-timestamps", is_flag=True, help="Timestamps por palavra no JSON.")
@click.option("--beam-size", type=int, default=None, help="Beam search (1 = mais rápido, 5 = padrão).")
@click.option("--prompt", "initial_prompt", default=None, help="Prompt inicial (melhora pontuação).")
@click.option("--no-vad", is_flag=True, help="Desliga a detecção de voz (VAD).")
@click.option("--stdout", "to_stdout", is_flag=True, help="Imprime a transcrição em vez de gravar arquivos.")
@click.option("--yes", is_flag=True, help="Não pedir confirmação para arquivos grandes.")
@click.pass_context
def cmd_transcribe(ctx: click.Context, paths: tuple[Path, ...], outdir: Path | None,
                   model: str | None, language: str | None, task: str | None,
                   formats: tuple[str, ...], no_preprocess: bool, loudnorm: bool,
                   word_timestamps: bool, beam_size: int | None, initial_prompt: str | None,
                   no_vad: bool, to_stdout: bool, yes: bool) -> None:
    """Transcreve um ou mais arquivos (ou todos os áudios de uma pasta)."""
    cfg = load_cfg(ctx)

    targets: list[Path] = []
    for p in paths:
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and is_media_file(f):
                    targets.append(f)
        elif is_media_file(p):
            targets.append(p)
        else:
            click.echo(f"ignorado (não parece áudio/vídeo): {p}", err=True)

    if not targets:
        raise click.ClickException("nenhum arquivo de áudio/vídeo encontrado")

    # overrides de configuração
    if model:
        cfg.model = model
    if language:
        cfg.language = "" if language == "auto" else language
    if task:
        cfg.task = task
    if word_timestamps:
        cfg.word_timestamps = True
    if beam_size:
        cfg.beam_size = beam_size
    if initial_prompt:
        cfg.initial_prompt = initial_prompt
    if no_vad:
        cfg.vad = False
    cfg.__post_init__()

    fmts = tuple(formats) if formats else cfg.formats
    engine = Engine(cfg)

    total_bytes = sum(t.stat().st_size for t in targets)
    click.echo(click.style("transcritor", fg="cyan", bold=True)
               + f"  modelo {click.style(cfg.model, fg='green')}"
               f" · {cfg.device}/{cfg.compute_type} · {cfg.cpu_threads} threads"
               f" · idioma {cfg.language or 'auto'} · {len(targets)} arquivo(s) · {_human_bytes(total_bytes)}")

    if not cfg.local_model_path:
        click.echo(click.style(
            f"  (!) modelo '{cfg.model}' ainda não está em {cfg.model_dir}; será baixado agora",
            fg="yellow"))

    pipeline = Pipeline(cfg, engine)
    rcs: dict[str, int] = {}

    for idx, target in enumerate(targets, 1):
        info = probe(target)
        click.echo("")
        click.echo(click.style(f"[{idx}/{len(targets)}] {target.name}", bold=True)
                   + f"  {_human_bytes(info.size_bytes)}"
                   + (f" · {ts_human(info.duration)}" if info.duration else "")
                   + ("" if info.has_audio else click.style(" · sem áudio detectado", fg="yellow")))

        def stage(phase: str, extra: dict, metadados=info) -> None:
            # `metadados` vem como valor padrão para fixar o arquivo desta
            # iteração — sem isso o closure leria o `info` do próximo laço.
            if phase == "progress":
                pct = extra.get("percent")
                pos = extra.get("position") or 0
                dur = extra.get("duration") or metadados.duration or 0
                eta = extra.get("eta")
                speed = extra.get("speed") or 0
                suffix = ""
                if eta is not None:
                    suffix = f"  faltam ~{ts_human(eta)}"
                if speed:
                    suffix += f"  {speed:.2f}x"
                sys.stdout.write("\r  transcrevendo " + _bar(pct)
                                 + f"  {ts_human(pos)}/{ts_human(dur)}" + suffix + "   ")
                sys.stdout.flush()
            elif phase == "probing":
                sys.stdout.write("\r  lendo metadados…" + " " * 50)
                sys.stdout.flush()
            elif phase == "normalizing":
                sys.stdout.write("\r  normalizando áudio (ffmpeg)…" + " " * 40)
                sys.stdout.flush()
            elif phase == "writing":
                sys.stdout.write("\r" + " " * 90 + "\r")
                sys.stdout.write("  gravando saídas…\n")
                sys.stdout.flush()

        t0 = time.time()
        try:
            out = pipeline.transcribe_file(
                target,
                stage=stage,
                formats=fmts,
                outdir=outdir,
                preprocess=not no_preprocess,
                loudnorm=loudnorm,
            )
        except Exception as exc:
            click.echo(click.style(f"  falhou: {exc}", fg="red"))
            rcs[str(target)] = 1
            continue

        sys.stdout.write("\r" + " " * 95 + "\r")
        result = out["result"]

        if to_stdout:
            click.echo(result.text)
            continue

        click.echo(click.style("  pronto", fg="green", bold=True)
                   + f"  {ts_human(result.duration)} de áudio em {ts_human(time.time() - t0)}"
                   f"  ({result.speed:.2f}x tempo real, {len(result.segments)} segmentos,"
                   f" {len(result.text.split())} palavras)")
        click.echo(f"  idioma detectado: {result.language} "
                   f"({result.language_probability:.0%} de confiança)")
        click.echo(f"  saída: {out['outdir']}")
        for f, path in out["outputs"].items():
            if f == "meta":
                continue
            click.echo(f"    · {Path(path).name}")

        if len(paths) == 1 and out["result"].text:
            preview = " ".join(out["result"].text.split())[:300]
            click.echo("")
            click.echo(click.style("  prévia: ", fg="cyan") + preview + ("…" if len(out["result"].text) > 300 else ""))

    if rcs:
        raise SystemExit(1)


# --------------------------------------------------------------------------
# watch
# --------------------------------------------------------------------------

@cli.command("watch", short_help="Vigia a pasta de entrada e transcreve sozinho.")
@click.option("--once", is_flag=True, help="Uma passada só e sai (útil em cron).")
@click.option("-i", "--interval", type=float, default=None, help="Intervalo entre varreduras (s).")
@click.option("-w", "--workers", type=int, default=None, help="Workers paralelos.")
@click.option("-f", "--format", "formats", multiple=True,
              type=click.Choice(["txt", "srt", "vtt", "json", "md"]))
@click.option("--delete-source", is_flag=True, help="Apaga o original após transcrever.")
@click.option("-m", "--model", default=None)
@click.option("--loudnorm", is_flag=True)
@click.pass_context
def cmd_watch(ctx: click.Context, once: bool, interval: float | None, workers: int | None,
              formats: tuple[str, ...], delete_source: bool, model: str | None,
              loudnorm: bool) -> None:
    """Fica de olho em var/inbox: o que aparecer é transcrito automaticamente."""
    cfg = load_cfg(ctx)
    if interval:
        cfg.watch_interval = interval
    if workers:
        cfg.watch_workers = workers
    if delete_source:
        cfg.watch_delete_source = True
    if model:
        cfg.model = model
        cfg.__post_init__()
    fmts = tuple(formats) if formats else cfg.formats

    store = JobStore(cfg)
    runner = Runner(cfg, store, workers=cfg.watch_workers, formats=fmts, loudnorm=loudnorm)
    watcher = Watcher(cfg, store, runner)

    if once:
        # Sem threads: a primeira passada só registra o estado dos arquivos; a
        # segunda, após o tempo de estabilização, enfileira os já copiados. Os
        # jobs são então processados em série na própria thread principal.
        watcher.scan_once(submit=False)
        if any(cfg.inbox.glob("**/*")):
            time.sleep(max(0.5, cfg.watch_settle_seconds) + 0.5)
        n = watcher.scan_once(submit=False)
        pending = list(reversed(store.all(status="queued")))
        click.echo(f"{n} arquivo(s) novo(s); {len(pending)} job(s) na fila")
        for j in pending:
            runner._run_job(j.id)
        counts = store.counts()
        click.echo(f"fim: {counts['done']} concluído(s), {counts['error']} erro(s)")
        store.save(force=True)
        return

    click.echo(click.style("vigilância ativa", fg="green", bold=True))
    click.echo(f"  entrada : {cfg.inbox}")
    click.echo(f"  saída   : {cfg.outbox}")
    click.echo(f"  modelo  : {cfg.model} · intervalo {cfg.watch_interval}s · {cfg.watch_workers} worker(s)")
    click.echo(click.style("  Ctrl+C para encerrar", fg="yellow"))

    def on_event(event: str, data: dict) -> None:
        stamp = time.strftime("%H:%M:%S")
        if event == "engine_ready":
            click.echo(f"[{stamp}] motor pronto em {data.get('seconds')}s")
        elif event == "engine_error":
            click.echo(f"[{stamp}] " + click.style(f"falha no motor: {data.get('error')}", fg="red"))
        elif event == "job_start":
            click.echo(f"[{stamp}] ▶ {data.get('filename')}")
        elif event == "job_progress":
            if data.get("phase") == "transcribing" and data.get("percent") is not None:
                pct = data["percent"]
                sys.stdout.write(f"\r        {_bar(pct)} {data.get('speed', 0):.2f}x   ")
                sys.stdout.flush()
        elif event == "job_done":
            sys.stdout.write("\r" + " " * 70 + "\r")
            click.echo(f"[{stamp}] " + click.style("✔", fg="green")
                       + f" {data.get('job_id')} → {data.get('outdir')} ({data.get('words')} palavras)")
        elif event == "job_error":
            sys.stdout.write("\r" + " " * 70 + "\r")
            click.echo(f"[{stamp}] " + click.style("✘", fg="red") + f" {data.get('error')}")

    runner.on_event = on_event
    runner.start()
    runner.submit_pending()
    watcher.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        click.echo("\nencerrando…")
    finally:
        watcher.stop()
        runner.stop()
        store.save(force=True)


# --------------------------------------------------------------------------
# serve / tui
# --------------------------------------------------------------------------

@cli.command("serve", short_help="Sobe a interface web local.")
@click.option("--host", default=None, help="Endereço (padrão 127.0.0.1).")
@click.option("-p", "--port", type=int, default=None, help="Porta (padrão 8765).")
@click.option("--open", "open_browser", is_flag=True, help="Abrir o navegador.")
@click.option("--no-watch", is_flag=True, help="Não vigiar a pasta de entrada.")
@click.option("-m", "--model", default=None)
@click.option("-f", "--format", "formats", multiple=True,
              type=click.Choice(["txt", "srt", "vtt", "json", "md"]))
@click.pass_context
def cmd_serve(ctx: click.Context, host: str | None, port: int | None, open_browser: bool,
              no_watch: bool, model: str | None, formats: tuple[str, ...]) -> None:
    """Interface web: upload por arrastar-e-soltar, progresso ao vivo, downloads."""
    cfg = load_cfg(ctx)
    if model:
        cfg.model = model
    if formats:
        cfg.formats = tuple(formats)
    cfg.__post_init__()

    from .web.server import serve as serve_web
    sys.exit(serve_web(cfg, host=host, port=port, open_browser=open_browser, watch=not no_watch))


@cli.command("tui", short_help="Painel interativo no terminal (curses).")
@click.option("--no-watch", is_flag=True, help="Não vigiar a pasta de entrada.")
@click.option("-m", "--model", default=None)
@click.pass_context
def cmd_tui(ctx: click.Context, no_watch: bool, model: str | None) -> None:
    """Painel com a fila, progresso e visualização da transcrição."""
    cfg = load_cfg(ctx)
    if model:
        cfg.model = model
        cfg.__post_init__()
    if not sys.stdout.isatty():
        raise click.ClickException("a TUI precisa de um terminal interativo")
    from .tui import run_tui
    sys.exit(run_tui(cfg, watch=not no_watch))


# --------------------------------------------------------------------------
# models
# --------------------------------------------------------------------------

@cli.group("models", short_help="Lista e baixa modelos Whisper.")
def grp_models() -> None:
    """Modelos CTranslate2 publicados no Hugging Face."""


@grp_models.command("list")
@click.pass_context
def cmd_models_list(ctx: click.Context) -> None:
    cfg = load_cfg(ctx)
    click.echo(f"pasta de modelos: {cfg.models_dir}")
    click.echo(f"modelo ativo    : {cfg.model}")
    click.echo("")
    click.echo(f"{'modelo':<16} {'baixado':<9} {'disco':>9} {'~tam':>8}  qualidade")
    click.echo("─" * 62)
    for m in model_status(cfg):
        mark = click.style("sim", fg="green") if m["downloaded"] else click.style("não", fg="yellow")
        active = click.style(" ←", fg="cyan", bold=True) if m["active"] else ""
        click.echo(f"{m['name']:<16} {mark:<9} {m['disk_mb']:>8.0f}M {m['approx_mb']:>7}M  {m['quality']}{active}")


@grp_models.command("download")
@click.argument("name", type=click.Choice([*KNOWN_MODELS, "repo/qualquer"]))
@click.option("--repo", default=None, help="Repositório HF arbitrário (Systran/faster-whisper-…).")
@click.pass_context
def cmd_models_download(ctx: click.Context, name: str, repo: str | None) -> None:
    """Baixa um modelo para var/../models (fica offline depois)."""
    cfg = load_cfg(ctx)
    target = repo or name

    def progress(info: dict) -> None:
        if info.get("phase") == "downloading":
            click.echo(f"baixando {info['repo']} → {info['path']}")

    click.echo(f"modelo: {target}  (~{KNOWN_MODELS.get(name, {}).get('size_mb', '?')} MB)")
    path = download_model(target, cfg, progress)
    click.echo(click.style(f"pronto: {path}", fg="green"))
    click.echo("use com:  transcritor transcribe -m " + name + " arquivo.mp3")


@grp_models.command("use")
@click.argument("name", type=click.Choice(list(KNOWN_MODELS)))
@click.pass_context
def cmd_models_use(ctx: click.Context, name: str) -> None:
    """Define o modelo padrão na configuração."""
    cfg = load_cfg(ctx)
    cfg.model = name
    cfg.__post_init__()
    path = cfg.save()
    click.echo(f"modelo padrão agora é {click.style(name, fg='green')} (salvo em {path})")
    if not cfg.local_model_path:
        click.echo(click.style("aviso: ainda não baixado — rode 'transcritor models download " + name + "'", fg="yellow"))


# --------------------------------------------------------------------------
# detect
# --------------------------------------------------------------------------

@cli.command("detect", short_help="Detecta o idioma de um áudio (rápido).")
@click.argument("paths", nargs=-1, required=True,
                type=click.Path(exists=True, dir_okay=True, path_type=Path))
@click.pass_context
def cmd_detect(ctx: click.Context, paths: tuple[Path, ...]) -> None:
    """Analisa os primeiros segundos e diz o idioma — sem transcrever tudo."""
    cfg = load_cfg(ctx)

    targets: list[Path] = []
    for p in paths:
        if p.is_dir():
            targets += [f for f in sorted(p.rglob("*")) if f.is_file() and is_media_file(f)]
        elif is_media_file(p):
            targets.append(p)

    if not targets:
        raise click.ClickException("nenhum arquivo de áudio/vídeo encontrado")

    engine = Engine(cfg)
    for t in targets:
        try:
            lang, prob = engine.detect_language(t)
        except Exception as exc:
            click.echo(f"{t.name:<40} " + click.style(f"falhou: {exc}", fg="red"))
            continue
        click.echo(f"{t.name:<40} " + click.style(f"{lang}", fg="cyan", bold=True)
                   + f"  ({prob:.0%} de confiança)")


# --------------------------------------------------------------------------
# jobs
# --------------------------------------------------------------------------

@cli.group("jobs", short_help="Consulta e gerencia o histórico de jobs.")
def grp_jobs() -> None:
    """Histórico persistido em var/state.json."""


@grp_jobs.command("list")
@click.option("--status", type=click.Choice(["queued", "running", "done", "error", "canceled"]))
@click.option("-n", "--limit", type=int, default=30)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def cmd_jobs_list(ctx: click.Context, status: str | None, limit: int, as_json: bool) -> None:
    cfg = load_cfg(ctx)
    store = JobStore(cfg)
    jobs = store.all(limit=limit, status=status)
    if as_json:
        click.echo(json.dumps([j.to_dict() for j in jobs], ensure_ascii=False, indent=2))
        return
    if not jobs:
        click.echo("sem jobs")
        return
    click.echo(f"{'id':<13} {'status':<11} {'dur':>8} {'palavras':>9}  arquivo")
    click.echo("─" * 78)
    colors = {"done": "green", "error": "red", "running": "cyan", "canceled": "yellow", "queued": "white"}
    for j in jobs:
        st = click.style(f"{j.status:<11}", fg=colors.get(j.status, "white"))
        click.echo(f"{j.id:<13} {st} {ts_human(j.duration):>8} {j.words:>9}  {j.filename[:38]}")


@grp_jobs.command("show")
@click.argument("job_id")
@click.option("--format", "fmt", type=click.Choice(["txt", "srt", "vtt", "json", "md"]), default="txt")
@click.pass_context
def cmd_jobs_show(ctx: click.Context, job_id: str, fmt: str) -> None:
    """Mostra a transcrição de um job."""
    cfg = load_cfg(ctx)
    store = JobStore(cfg)
    job = store.get(job_id)
    if not job:
        raise click.ClickException(f"job não encontrado: {job_id}")
    path = (job.outputs or {}).get(fmt)
    if not path or not Path(path).exists():
        raise click.ClickException(f"formato {fmt} indisponível para {job_id} (status {job.status})")
    click.echo(Path(path).read_text(encoding="utf-8"))


@grp_jobs.command("retry")
@click.argument("job_id", required=False)
@click.option("--failed", is_flag=True, help="Refaz todos os jobs que terminaram com erro.")
@click.option("-f", "--format", "formats", multiple=True)
@click.option("--yes", is_flag=True)
@click.pass_context
def cmd_jobs_retry(ctx: click.Context, job_id: str | None, failed: bool,
                   formats: tuple[str, ...], yes: bool) -> None:
    """Reprocessa um job (ou todos os que falharam, com --failed), em série."""
    cfg = load_cfg(ctx)
    store = JobStore(cfg)

    if failed:
        targets = store.all(status="error")
        if not targets:
            click.echo("nenhum job com erro")
            return
        if not yes:
            click.confirm(f"refazer {len(targets)} job(s) com erro?", abort=True)
        ids = [j.id for j in targets]
    elif job_id:
        ids = [job_id]
    else:
        raise click.ClickException("informe um job_id ou use --failed")

    fmts = tuple(formats) if formats else cfg.formats
    for jid in ids:
        job = store.get(jid)
        if not job:
            click.echo(f"não encontrado: {jid}", err=True)
            continue
        if not Path(job.source).exists():
            click.echo(f"arquivo original sumiu: {job.source}", err=True)
            continue
        store.update(jid, status="queued", error="", finished=None,
                     progress={"phase": "na fila"})
    store.save(force=True)

    runner = Runner(cfg, store, formats=fmts)
    runner.on_event = lambda e, d: None
    runner.start(warmup=False)
    for jid in ids:
        job = store.get(jid)
        if job and job.status == "queued":
            click.echo(f"→ {job.filename}")
            runner._run_job(jid)
    runner.stop()
    store.save(force=True)

    done = 0
    for jid in ids:
        j = store.get(jid)
        if j and j.status == "done":
            done += 1
    click.echo(click.style(f"{done}/{len(ids)} concluído(s)",
                           fg="green" if done == len(ids) else "yellow"))
    if done != len(ids):
        raise SystemExit(1)


@grp_jobs.command("rm")
@click.argument("job_id")
@click.pass_context
def cmd_jobs_rm(ctx: click.Context, job_id: str) -> None:
    """Remove um job do histórico (não apaga arquivos)."""
    cfg = load_cfg(ctx)
    store = JobStore(cfg)
    click.echo("removido" if store.remove(job_id) else "não encontrado")


@grp_jobs.command("clear")
@click.confirmation_option(prompt="Apagar do histórico todos os jobs finalizados?")
@click.pass_context
def cmd_jobs_clear(ctx: click.Context) -> None:
    """Limpa o histórico (mantém o que está na fila/rodando)."""
    cfg = load_cfg(ctx)
    store = JobStore(cfg)
    click.echo(f"{store.clear_finished()} job(s) removido(s)")


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------

@cli.group("config", short_help="Mostra e edita a configuração.")
def grp_config() -> None:
    """Configuração em $TRANSCRITOR_HOME/config.json (padrão ~/transcritor/config.json)."""


@grp_config.command("show")
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def cmd_config_show(ctx: click.Context, as_json: bool) -> None:
    cfg = load_cfg(ctx)
    if as_json:
        click.echo(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2))
        return
    for k, v in sorted(cfg.to_dict().items()):
        click.echo(f"{k:<28} {v}")


@grp_config.command("set")
@click.argument("pairs", nargs=-1, required=True)
@click.pass_context
def cmd_config_set(ctx: click.Context, pairs: tuple[str, ...]) -> None:
    """Define valores: transcritor config set model=large-v3 language=pt"""
    cfg = load_cfg(ctx)
    valid = set(cfg.__dataclass_fields__)  # type: ignore[attr-defined]
    for pair in pairs:
        if "=" not in pair:
            raise click.ClickException(f"esperado chave=valor, recebi {pair!r}")
        key, _, raw = pair.partition("=")
        key = key.strip()
        if key not in valid:
            raise click.ClickException(f"chave desconhecida: {key}")
        current = getattr(cfg, key)
        try:
            if isinstance(current, bool):
                value: object = raw.strip().lower() in ("1", "true", "yes", "on", "sim")
            elif isinstance(current, int):
                value = int(raw)
            elif isinstance(current, float):
                value = float(raw)
            elif isinstance(current, tuple):
                value = tuple(x.strip() for x in raw.split(",") if x.strip())
            else:
                value = raw
        except ValueError as exc:
            raise click.ClickException(f"valor inválido para {key}: {exc}") from exc
        setattr(cfg, key, value)
        click.echo(f"{key} = {value}")
    cfg.__post_init__()
    path = cfg.save()
    click.echo(click.style(f"salvo em {path}", fg="green"))


@grp_config.command("init")
@click.option("--force", is_flag=True)
@click.pass_context
def cmd_config_init(ctx: click.Context, force: bool) -> None:
    """Cria um config.json com todos os padrões (no caminho efetivo)."""
    cfg = load_cfg(ctx)
    path = cfg.config_path
    if path.exists() and not force:
        raise click.ClickException(f"já existe: {path} (use --force para sobrescrever)")
    example = Path(__file__).resolve().parent.parent / "config.example.json"
    if example.exists() and not force:
        path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    else:
        path.write_text(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    click.echo(click.style(f"escrito: {path}", fg="green"))


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

@cli.command("doctor", short_help="Diagnostica o ambiente e a instalação.")
@click.pass_context
def cmd_doctor(ctx: click.Context) -> None:
    """Checa ffmpeg, dependências, modelo, memória e permissões."""
    cfg = load_cfg(ctx)
    ok = True

    def check(label: str, condition: bool, detail: str = "", fix: str = "") -> None:
        nonlocal ok
        glyph = click.style("✔", fg="green") if condition else click.style("✘", fg="red")
        click.echo(f" {glyph} {label:<34} {detail}")
        if not condition:
            ok = False
            if fix:
                click.echo(f"     → {click.style(fix, fg='yellow')}")

    click.echo(click.style("transcritor doctor", bold=True))
    click.echo("")

    # python / libs
    import platform
    click.echo(click.style("Ambiente", bold=True))
    check("python", True, platform.python_version())
    try:
        import faster_whisper
        v = getattr(faster_whisper, "__version__", "ok")
        check("faster-whisper", True, str(v))
    except ImportError:
        check("faster-whisper", False, "não instalado", "pip install faster-whisper")
    try:
        import ctranslate2
        check("ctranslate2", True, ctranslate2.__version__)
    except ImportError:
        check("ctranslate2", False, "não instalado", "pip install ctranslate2")
    try:
        import jinja2
        check("jinja2 (web)", True, jinja2.__version__)
    except ImportError:
        check("jinja2 (web)", False, "não instalado", "pip install jinja2")

    check("ffmpeg", bool(shutil.which("ffmpeg")), shutil.which("ffmpeg") or "",
          "instale: sudo dnf install ffmpeg / apt install ffmpeg")
    check("ffprobe", bool(shutil.which("ffprobe")), shutil.which("ffprobe") or "")

    click.echo("")
    click.echo(click.style("Hardware", bold=True))
    cpu = os.cpu_count() or 0
    check("CPUs", cpu > 0, str(cpu))
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            mem = next(linha for linha in fh if linha.startswith("MemAvailable"))
        avail_gb = int(mem.split()[1]) / 1048576
        need = 2.5 if cfg.model.startswith("large") else 1.0
        check("memória disponível", avail_gb >= need, f"{avail_gb:.1f} GB",
              f"modelo {cfg.model} em int8 pede ~{need:.1f} GB livres")
    except (OSError, StopIteration):
        check("memória disponível", True, "não foi possível ler /proc/meminfo")
    has_gpu = shutil.which("nvidia-smi") is not None
    check("GPU CUDA (opcional)", True,
          "detectada — use device=cuda e compute_type=float16" if has_gpu
          else "não há — rodando em CPU com int8, que é o esperado")

    click.echo("")
    click.echo(click.style("Configuração", bold=True))
    check("pasta home", cfg.home.exists(), str(cfg.home))
    for name, d in (("inbox", cfg.inbox), ("outbox", cfg.outbox), ("work", cfg.work)):
        writable = d.exists() and os.access(d, os.W_OK)
        check(f"pasta {name} gravável", writable, str(d))
    check("config.json", True,
          str(cfg.home / "config.json") if (cfg.home / "config.json").exists()
          else "usando padrões (crie com 'transcritor config init')")
    check(f"modelo '{cfg.model}'", cfg.local_model_path is not None,
          cfg.local_model_path or "não baixado",
          f"baixe com: transcritor models download {cfg.model}")
    check("threads reservadas", cfg.cpu_threads > 0, str(cfg.cpu_threads))
    check("idioma", True, cfg.language or "auto-detect")

    click.echo("")
    click.echo(click.style("Histórico", bold=True))
    store = JobStore(cfg)
    c = store.counts()
    check("state.json", True,
          f"{c['total']} job(s): {c['queued']} fila, {c['done']} prontos, {c['error']} erros")

    click.echo("")
    if ok:
        click.echo(click.style("tudo certo — pode transcrever.", fg="green", bold=True))
    else:
        click.echo(click.style("há pendências acima.", fg="yellow", bold=True))
        raise SystemExit(1)


# --------------------------------------------------------------------------
# info
# --------------------------------------------------------------------------

@cli.command("info", short_help="Resumo de caminhos e do modelo ativo.")
@click.pass_context
def cmd_info(ctx: click.Context) -> None:
    cfg = load_cfg(ctx)
    store = JobStore(cfg)
    c = store.counts()
    click.echo(click.style("transcritor", fg="cyan", bold=True) + f" v{__version__}")
    click.echo(f"  home         : {cfg.home}")
    click.echo(f"  entrada      : {cfg.inbox}")
    click.echo(f"  saída        : {cfg.outbox}")
    click.echo(f"  modelos      : {cfg.models_dir}")
    click.echo(f"  estado       : {cfg.state_file}")
    click.echo(f"  modelo ativo : {cfg.model} ({'baixado' if cfg.local_model_path else 'NÃO baixado'})")
    click.echo(f"  dispositivo  : {cfg.device}/{cfg.compute_type}, {cfg.cpu_threads} threads")
    click.echo(f"  idioma/tarefa: {cfg.language or 'auto'} / {cfg.task}")
    click.echo(f"  formatos     : {', '.join(cfg.formats)}")
    click.echo(f"  jobs         : {c['total']} total — {c['queued']} na fila, "
               f"{c['running']} rodando, {c['done']} prontos, {c['error']} com erro")
    click.echo(f"  web          : http://{cfg.web_host}:{cfg.web_port}/")


def main() -> None:
    # prog_name fixo: garante o mesmo nome em qualquer forma de invocação
    # (script, link simbólico, `python -m transcritor`) e faz o autocompletar
    # do shell funcionar com _TRANSCREVER_COMPLETE.
    cli(prog_name="transcrever", obj={})


if __name__ == "__main__":
    main()
