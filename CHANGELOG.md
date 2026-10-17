# Histórico de alterações

Todas as mudanças relevantes deste projeto são documentadas aqui.
O formato segue [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/)
e o projeto adota [Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [1.1.1] - 2026-10-17

Primeira publicação no PyPI.

### Corrigido

- A transcrição não quebra mais com o PyAV 19. Quem decodifica o áudio passa a ser o **ffmpeg**,
  e o modelo recebe as amostras prontas (16 kHz, mono, float32). O PyAV 19 removeu o argumento
  `metadata_errors`, que o faster-whisper 1.2.1 ainda usa ao abrir o arquivo: como o `pip` já
  instala o PyAV 19 no Python 3.12 ou mais novo, quem instalasse hoje falhava na primeira
  transcrição com `unexpected keyword argument 'metadata_errors'`.
- O PyAV deixou de ser dependência direta (ele continua vindo com o faster-whisper, que o
  importa, mas o transcritor não o usa mais).

### Adicionado

- Pacote publicado no [PyPI](https://pypi.org/project/transcritor/) com os comandos `transcrever` e
  `transcritor`. `pipx install transcritor` passa a ser a instalação mais rápida — o instalador de
  uma linha já preferia o PyPI e agora o encontra.
- Publicação automática a partir das etiquetas, por Trusted Publishing (sem token guardado), com
  atestados digitais dos artefatos e envio somente depois de `twine check` e da suíte de testes.
- Verificação de empacotamento na integração contínua: a cada envio, a wheel e o pacote de
  código-fonte são construídos, conferidos e instalados em ambientes limpos; a suíte roda contra a
  wheel instalada, fora do repositório, para provar que o que foi publicado funciona.
- Testes que exercitam o motor de verdade (com um modelo dublê) e a decodificação do áudio — era
  exatamente o caminho sem cobertura que deixou o problema do PyAV passar.
- Testes que conferem o empacotamento sem construir nada: versão do `pyproject.toml` igual à do
  pacote, `requirements.txt` igual às dependências do `pyproject.toml`, comandos e dados declarados.
- `MANIFEST.in`, para que o pacote de código-fonte traga testes, documentação, o instalador e os
  dados da interface junto do código — o que também serve de base para os pacotes RPM/DEB.

### Alterado

- Metadados no padrão atual: licença como expressão SPDX (PEP 639), Linux e macOS declarados e
  `Documentation` apontando para o README.
- No README, links e imagens passam a ser absolutos: no PyPI, caminho relativo quebra a imagem e a
  página fica sem a logo e as capturas.

[1.1.1]: https://github.com/a32fred/transcritor/releases/tag/v1.1.1

## [1.1.0] - 2026-10-17

Instalação e primeiro uso pensados para quem não tem conhecimento técnico.

### Adicionado

- **Instalador de uma linha** (`instalar.sh`): descobre a distribuição, instala FFmpeg, Python e `pipx`,
  instala o programa isolado, cria o ícone do menu e o “Abrir com”, oferece o download do modelo
  e confere o resultado com `doctor`. Opções `--sim`, `--sem-modelo`, `--modelo`, `--sem-icone`,
  `--versao`, `--dry-run` e `--help`.
- **Menu interativo**: `transcrever` sem argumentos, em um terminal, abre um menu numerado
  (transcrever um arquivo ou pasta, abrir a interface, baixar ou trocar o modelo, diagnóstico).
  Fora de um terminal, continua mostrando a ajuda.
- **Download do modelo guiado**: o tamanho e o tempo são informados antes de baixar, com barra de
  progresso real em porcentagem; nada é baixado em silêncio durante a transcrição.
- **Comando `abrir`**: enfileira arquivos ou pastas (sem copiá-los) e abre a interface no navegador,
  subindo o servidor em segundo plano quando necessário. É o que o ícone do menu e o “Abrir com” usam.
- **Comando `desktop`**: `instalar`, `remover` e `status` para o ícone no menu e a entrada
  “Abrir com o transcritor” nos formatos de áudio e vídeo reconhecidos.
- **Download do modelo pela interface web**: faixa na página quando falta o modelo, com tamanho,
  barra de progresso e estado em `/api/status` (`model_download`, `model_size_mb`, `model_quality`)
  e no novo `POST /api/models/download` (responde `409` se já houver download em andamento).
- `transcrever models download NOME --usar`: baixa e já torna o modelo o padrão.
- Chave `-h` como atalho de `--help`.

### Alterado

- **Dados separados do código**: por padrão, a base passa a ser `~/.local/share/transcritor`
  (ou `$XDG_DATA_HOME/transcritor`). Quem já tem `~/transcritor/var`, `~/transcritor/models` ou
  `~/transcritor/config.json` continua nesse layout — nada é movido nem perdido.
- Barras de progresso e mensagens técnicas de bibliotecas de terceiros (`huggingface_hub`, `httpx`)
  deixam de poluir a saída em uso normal.
- Serviço systemd de exemplo passa a usar o executável `transcrever` instalado, com instrução de
  override para quem trabalha a partir do repositório.
- README reescrito na parte de instalação: um comando, três formas de usar sem decorar comando,
  onde ficam os dados, como atualizar e desinstalar.

### Corrigido

- `download_model` não informava progresso: agora mede o que chegou ao disco e reporta porcentagem.
- A faixa de download da interface web ficava presa em “em andamento” depois de concluir.

[1.1.0]: https://github.com/a32fred/transcritor/releases/tag/v1.1.0

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
