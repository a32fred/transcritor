# Como contribuir

Obrigado por contribuir com o transcritor! Siga nosso [Código de Conduta](CODE_OF_CONDUCT.md).

## Preparar o ambiente

Use Python 3.10 ou superior e instale `ffmpeg` (inclui `ffprobe`). No Debian/Ubuntu:

```shell
sudo apt-get update
sudo apt-get install ffmpeg
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
pip install pytest ruff
```

No Windows, ative com `.venv\Scripts\activate` e instale o ffmpeg pelo gerenciador de sua preferência,
garantindo que `ffmpeg` e `ffprobe` estejam no `PATH`.

O comando instalado é `transcritor`; o projeto também fornece `bin/transcrever`.
Para usar os exemplos com esse nome no Linux/macOS, execute na raiz:

```shell
export PATH="$PWD/bin:$PATH"
transcrever --help
```

Como alternativa, use `transcritor` ou `python -m transcritor` nos mesmos exemplos.

## Testes e lint

Execute na raiz do projeto:

```shell
python -m pytest tests/ -q
ruff check transcritor tests
```

Os testes usam um motor dublado (`FakeEngine`): não precisam de GPU, não baixam modelos e não exigem
transcrições reais. Testes de mídia são pulados automaticamente quando não há ffmpeg.
Para interromper na primeira falha, use `python -m pytest tests/ -x -q`.
Para cobertura opcional, instale `pytest-cov` e execute `make cov`.

O Makefile reúne os atalhos; consulte `make help`. Antes de formatar, confira o impacto de
`ruff format transcritor tests` e evite alterações de estilo sem relação com sua contribuição.

## Convenções de código

- Use nomes de módulos, classes, funções e variáveis em inglês.
- Escreva docstrings, comentários explicativos e mensagens ao usuário em português do Brasil.
- Preserve os padrões existentes, com indentação Python de quatro espaços, UTF-8 e finais de linha LF.
- Prefira linhas de até 110 caracteres, funções pequenas e testes determinísticos.
- Inclua testes de regressão para correções e não introduza downloads de modelos na suíte automatizada.
- Não inclua modelos, áudios pessoais, transcrições, configurações locais, `.venv/` ou arquivos de `var/`.

## Commits

Use [Conventional Commits](https://www.conventionalcommits.org/pt-br/v1.0.0/), com descrições em PT-BR
de forma consistente. Mantenha os tipos técnicos em inglês:

```text
fix(watch): evita processamento duplicado
feat(cli): adiciona opção de saída
docs: esclarece instalação do ffmpeg
test: cobre recuperação da fila
```

## Issues e pull requests

1. Procure relatos existentes antes de abrir uma [issue](https://github.com/a32fred/transcritor/issues).
2. Use o formulário de erro ou sugestão. Para erros, inclua reprodução mínima, resultado esperado,
   ambiente e saídas de `transcrever info` e `transcrever doctor`; remova dados pessoais dos diagnósticos.
3. Para mudanças grandes, discuta a proposta em uma issue antes de implementar.
4. Crie uma branch no seu fork, faça mudanças focadas e inclua os testes pertinentes.
5. Execute testes e lint; atualize README e CHANGELOG quando aplicável.
6. Abra o PR para `main`, preencha o modelo, vincule a issue e explique como validar a mudança.

Os mantenedores revisam os PRs e podem solicitar ajustes antes de integrar. Para vulnerabilidades,
**não abra uma issue pública**: siga [SECURITY.md](SECURITY.md).

## Testar uma transcrição real (opcional)

Use uma gravação curta em PT-BR que você tenha autorização para processar:

```shell
transcrever models download tiny
transcrever transcribe -m tiny -l pt -f txt -f srt caminho/amostra.wav
```

O download inicial requer internet; depois, a transcrição usa o modelo local, sem enviar áudio.
O `tiny` é útil para um teste rápido em CPU, mas tem qualidade inferior ao padrão `large-v3`.
Confira o texto e os tempos das legendas na saída indicada pelo comando. Não envie esses arquivos no PR.
Esse teste manual não é requisito da suíte automatizada nem da CI.
