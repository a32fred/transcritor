# Como contribuir

Obrigado por contribuir com o transcritor! Siga nosso [Código de Conduta](CODE_OF_CONDUCT.md).

## Usar ou desenvolver?

Se você só quer **usar** o transcritor, este documento não é para você: siga a
[instalação em um comando](README.md#instalação-em-um-comando) no README. O que vem
abaixo monta um ambiente de desenvolvimento a partir do código-fonte.

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
pip install pytest "ruff==0.17.1"
```

No Windows, ative com `.venv\Scripts\activate` e instale o ffmpeg pelo gerenciador de sua preferência,
garantindo que `ffmpeg` e `ffprobe` estejam no `PATH`.

A versão do `ruff` é fixada de propósito: o conjunto de regras e o comportamento de algumas delas
mudam entre versões, e o CI usa exatamente esta. Ao atualizar a versão, faça isso em um commit
próprio, com o CI verde, e ajuste `.github/workflows/ci.yml` e `pyproject.toml` juntos.

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

## Publicar uma versão (mantenedores)

O transcritor é publicado no [PyPI](https://pypi.org/project/transcritor/) a partir de uma etiqueta.
O envio usa [Trusted Publishing](https://docs.pypi.org/trusted-publishers/): o PyPI confia neste
repositório, no arquivo `.github/workflows/publish.yml` e no ambiente `pypi`, então **não existe
token guardado** — nem no GitHub, nem na sua máquina. O token é criado na hora, e cada arquivo sai
com um atestado digital de que foi construído aqui, a partir desta etiqueta.

Passo a passo:

1. Atualize a versão em `transcritor/__init__.py` **e** em `pyproject.toml` (o teste
   `tests/test_empacotamento.py` falha se elas divergirem) e registre a mudança no `CHANGELOG.md`.
2. Envie para `main` e espere a CI ficar verde — o trabalho `empacotamento` constrói a wheel e o
   pacote de código-fonte, confere os metadados e roda a suíte contra a wheel instalada.
3. Crie a etiqueta e o lançamento:
   ```shell
   git tag -a v1.1.1 -m "transcritor 1.1.1"
   git push origin v1.1.1
   gh release create v1.1.1 --title "transcritor 1.1.1" --notes-from-tag
   ```
4. O lançamento publicado dispara o `publish.yml`, que constrói, confere e envia ao PyPI. Acompanhe
   em <https://github.com/a32fred/transcritor/actions>.

Duas coisas são irreversíveis: **um número de versão não pode ser reaproveitado** no PyPI, e um
arquivo publicado não pode ser substituído (no máximo retirado do ar). Se algo sair errado depois do
envio, corrija e publique a versão seguinte.

Para ensaiar antes, sem gastar um número: use o [TestPyPI](https://test.pypi.org/) com
`python -m build` e `uv publish --publish-url https://test.pypi.org/legacy/`, e instale de lá com
`pipx install --index-url https://test.pypi.org/simple/ transcritor`. O nome precisa existir também
no TestPyPI, com o mesmo Trusted Publisher apontando para lá.

