# Histórico de alterações

Todas as mudanças relevantes deste projeto são documentadas aqui.
O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e o projeto adota [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [1.0.0] - 2026-10-17

### Adicionado

- Transcrição offline com faster-whisper e CTranslate2, usando CPU com `int8` e modelo padrão `large-v3`.
- Idioma e prompts voltados ao português do Brasil.
- Três interfaces: CLI, TUI no terminal e interface web local.
- Pasta vigiada com espera pela estabilização dos arquivos antes do processamento.
- Fila de jobs persistente com recuperação após queda.
- Saída em `txt`, `srt`, `vtt`, `json` e `md`.
- Reconhecimento de 23 extensões de áudio e vídeo.
- Normalização de áudio via ffmpeg, incluindo opção de normalização de volume.
- Download e troca de modelos de transcrição.

### Corrigido

- Processamento duplicado no comando `watch --once`.
- Colisão de nomes no parâmetro `phase` durante notificações de progresso.
- Opção `--config` ignorada ao salvar a configuração.
- Quebra desbalanceada de cues nas legendas SRT.
- Criação de `Runner` sem o argumento `store`.
- Erro críptico ao processar um arquivo sem faixa de áudio.
- Nomes pouco legíveis quando há colisão de pasta de saída.
- Tracebacks ruidosos de `BrokenPipeError` no servidor web quando o cliente desconecta.
- Variável `TRANSCRITOR_HOME` ignorada ao carregar configuração sem `config.json`.

### Segurança

- Processamento local, sem envio de áudio a serviços externos.
- Servidor web restrito a `127.0.0.1` por padrão; não possui autenticação e não deve ser exposto
  diretamente à rede. Consulte a [política de segurança](SECURITY.md) para limites e recomendações.

[1.0.0]: https://github.com/a32fred/transcritor/releases/tag/v1.0.0
