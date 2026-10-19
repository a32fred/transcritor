# Pacotes Linux (.rpm e .deb)

Os pacotes de distribuição instalam o transcritor pelo gerenciador de pacotes, para
todos os usuários da máquina, com o ícone no menu e o "Abrir com o transcritor" já
prontos. Este documento explica por que eles são montados do jeito que são, como
construir, como conferir e como publicar.

## Por que o pacote carrega as dependências

Um pacote de distribuição normalmente declara o que precisa (`Requires:`) e deixa o
gerenciador resolver. Aqui isso não é possível.

No Fedora 44, os repositórios têm `python3-numpy`, `python3-onnxruntime` e
`python3-tokenizers`, mas **não** têm `faster-whisper`, `ctranslate2` nem `PyAV` —
que são justamente as três peças do motor. No Debian e no Ubuntu a situação é
parecida. Um pacote feito só de `Requires` não teria como ser instalado.

Então o pacote monta o seu próprio ambiente, a partir das wheels publicadas no PyPI,
e o guarda em `/usr/lib64/transcritor/venv`. O que isso dá:

* o Python do sistema não é tocado e não existe conflito de versões com outros
  pacotes;
* `dnf install` / `apt install` funciona sem pipx e sem internet;
* o único download que resta é o do modelo, na primeira transcrição — e quem
  escolhe é quem usa (de 75 MB, no `tiny`, a 3 GB no `large-v3`).

O preço: ainda é um pacote grande (RPM: ~87 MB comprimido/~412 MiB instalado;
`.deb`: ~71 MB/~320 MiB instalado) e as versões das bibliotecas são as que estavam
publicadas no dia da construção. Atualizações chegam com uma versão nova do pacote
— o que, para quem instala por gerenciador de pacotes, é exatamente o esperado.

O build resolve as versões compatíveis disponíveis no PyPI naquele momento; por
isso, os pacotes anexados a um lançamento são os artefatos aprovados pela CI, não
um convite a reconstruir o mesmo número de versão meses depois e esperar bytes
idênticos. Uma nova versão do transcritor reconstrói os pacotes e repete os testes.

Nada disso substitui o caminho do PyPI (`pipx install transcritor`), que continua
sendo o mais leve e o único que atualiza cada peça por conta própria.

## Como a versão do pacote é mantida

A versão mora em `packaging/rpm/transcritor.spec` (`Version:`) e precisa ser a mesma
do `pyproject.toml` e do pacote. O teste
`tests/test_empacotamento.py::test_versao_do_spec_do_rpm_bate_com_o_pacote` falha se
elas saírem de sincronia.

## RPM (Fedora, RHEL e derivados)

### Instalar um pacote pronto

```bash
sudo dnf install ./transcritor-1.1.1-1.fc44.x86_64.rpm
```

O `dnf` resolve o que falta (o `ffmpeg-free` e o `python3`), escreve o comando em
`/usr/bin/transcrever`, instala o ícone e a entrada "Abrir com o transcritor" em
`/usr/share/applications` e atualiza os caches da área de trabalho.

### Construir

```bash
./packaging/rpm/build.sh                 # deixa o .rpm em dist/
./packaging/rpm/build.sh --outdir /tmp   # em outro diretório
```

O script constrói o sdist a partir do código (`--sdist ARQUIVO` usa um arquivo
específico, que é o que a integração contínua faz, para o pacote sair exatamente do
mesmo sdist que foi conferido), monta um `_topdir` temporário e chama o `rpmbuild` —
nada é escrito no `~/rpmbuild` do seu usuário. Precisa de `rpmbuild`,
`python3-devel` e `python3-pip` instalados, e de internet para baixar as wheels.

### Conferir

```bash
rpm -qpl dist/transcritor-*.rpm                  # o que vai dentro
rpm -qp --requires dist/transcritor-*.rpm        # do que depende
```

O teste que vale é instalar num Fedora limpo. Com contêiner, sem tocar na sua
máquina:

```bash
podman run --rm -v "$PWD/dist:/mnt:ro" fedora:latest bash -c \
  'dnf install -y /mnt/transcritor-*.rpm && transcrever --version && transcrever info'
```

Para conferir uma transcrição de verdade, monte também a pasta do modelo (veja
`transcrever info` para saber onde ele está) e rode `transcrever transcribe`.

### Publicar no COPR

O [COPR](https://copr.fedorainfracloud.org/) é o caminho para quem usa Fedora ter
`dnf upgrade` cuidando das atualizações. O spec já está no repositório, e o build
por SCM aponta para ele:

1. Crie a conta em <https://copr.fedorainfracloud.org/> e um projeto (por exemplo
   `a32fred/transcritor`), marcando os chroots que devem ser construídos.
2. Na aba **Builds**, escolha **SCM**, informe
   `https://github.com/a32fred/transcritor.git`, o ramo `main` e o caminho
   `packaging/rpm/transcritor.spec`.
3. Quem for instalar faz:

   ```bash
   sudo dnf copr enable a32fred/transcritor
   sudo dnf install transcritor
   ```

O spec baixa as wheels do PyPI durante a construção. Se o build por SCM não tiver
rede no COPR, ele falha nesse passo — nesse caso, construa pela integração contínua
(trabalho `pacotes`, que tem rede e já confere o resultado) e anexe o `.rpm` ao
lançamento no GitHub, ou envie o pacote já construído para o projeto no COPR.

## .deb (Debian, Ubuntu e derivados)

O construtor está em `packaging/deb/build.sh`. Ele monta um ambiente isolado em
`/usr/lib/transcritor/venv`, o comando em `/usr/bin/transcrever`, a entrada de menu e
o "Abrir com" em `/usr/share`. A lista MIME vem da mesma implementação usada pela
instalação por usuário.

Em Debian ou Ubuntu, com `dpkg-deb`, `dpkg`, `tar` e pip instalados:

```bash
./packaging/deb/build.sh
sudo apt install ./dist/transcritor_1.1.1-1_amd64.deb
```

Pode-se passar o sdist já conferido, outro diretório ou outro executável Python:

```bash
./packaging/deb/build.sh --sdist dist/transcritor-1.1.1.tar.gz
./packaging/deb/build.sh --outdir /tmp
./packaging/deb/build.sh --python python3.12
```

O construtor inclui os textos de licença das wheels e declara dependência da
versão minor do Python usada no build (os módulos compilados exigem o mesmo ABI),
do `libc6` compatível com as wheels manylinux e do FFmpeg. Assim, `apt` não tenta
misturar bibliotecas compiladas para versões diferentes do Python.

O pacote é construído em arquitetura nativa (`dpkg --print-architecture`); o
artefato para outra arquitetura deve ser construído em um host/runner daquela
arquitetura. A distribuição ao usuário será por anexo no lançamento do GitHub
(`sudo apt install ./transcritor_*.deb`), já que o projeto não tem repositório APT
próprio.

A CI constrói o `.deb` a partir do mesmo sdist conferido, instala com `dpkg` num
runner Ubuntu limpo e verifica o comando e os arquivos da área de trabalho. O
teste de transcrição real é feito no RPM Fedora 44; ambos empacotam o mesmo
aplicativo e as mesmas wheels.

## Detalhes do spec que valem explicação

Quem for mexer no `packaging/rpm/transcritor.spec` encontra estas decisões, todas
com comentário no arquivo:

* `__debug_install_post %{nil}`, `_build_id_links none` e `_debugsource_packages`
  removida: as bibliotecas embutidas são wheels de terceiros, sem nota de build ID.
  O Fedora exige essa nota para extrair "debug info" e o build quebra sem essas
  linhas. O `strip` continua acontecendo.
* `__requires_exclude_from` e `__provides_exclude_from` sobre o ambiente interno: as
  wheels trazem sonames "consertados" pelo auditwheel, que só existem dentro do
  pacote. Sem filtrar, o `rpmbuild` deduz dependências dessas bibliotecas e o
  `dnf install` passa a exigir do sistema o que está no próprio pacote.
* `python(abi) = %{python3_version}`: as bibliotecas compiladas valem para o ABI do
  Python com que foram instaladas. É o que garante que o pacote só instale onde o
  intérprete é compatível.
* `/usr/bin/ffmpeg` e `/usr/bin/ffprobe` como dependência de arquivo, e não de
  pacote: assim funciona tanto com o `ffmpeg-free` do Fedora quanto com o `ffmpeg`
  do RPM Fusion.
* as licenças de tudo que vai embutido são copiadas para
  `/usr/share/licenses/transcritor` — é código de terceiros dentro do pacote.
* a lista de tipos de arquivo do "Abrir com" e o comando do ícone vêm do próprio
  transcritor (`transcrever desktop instalar --destino ... --exec ...`), não de uma
  cópia que envelheceria dentro do empacotamento.
