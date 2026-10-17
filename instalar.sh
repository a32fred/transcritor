#!/usr/bin/env bash
#
# instalador do transcritor — para quem não quer configurar nada à mão.
#
#   curl -fsSL https://raw.githubusercontent.com/a32fred/transcritor/main/instalar.sh | bash
#
# O que ele faz, em ordem:
#   1. descobre a distribuição e instala o que falta (ffmpeg, python3, pipx);
#   2. instala o transcritor isolado, com pipx — sem ambiente virtual manual,
#      sem link simbólico, sem variável de ambiente para decorar;
#   3. cria o ícone no menu e a opção "Abrir com o transcritor";
#   4. oferece baixar o modelo (é o passo grande: de 0,5 a 3 GB);
#   5. roda o diagnóstico e diz, em três linhas, como usar.
#
# Opções:
#   --sim             não pergunta nada (baixa o modelo sugerido)
#   --sem-modelo      não baixa modelo agora
#   --modelo NOME     small | medium | large-v3-turbo | large-v3 (padrão: large-v3-turbo)
#   --sem-icone       não cria o ícone do menu nem o "Abrir com"
#   --versao REF      ramo ou etiqueta do repositório (padrão: main)
#   --dry-run         mostra o que faria, sem alterar nada
#   -h, --help        esta ajuda
#
# Pode ser executado de novo sem medo: ele atualiza a instalação existente.

set -euo pipefail

VERSAO="main"
MODELO="large-v3-turbo"
BAIXAR_MODELO="perguntar"
CRIAR_ICONE=1
SIM=0
DRY=0
REPO="https://github.com/a32fred/transcritor.git"
URL_SCRIPT="https://raw.githubusercontent.com/a32fred/transcritor/main/instalar.sh"

# ---------------------------------------------------------------- utilitários
negrito() { printf '\033[1m%s\033[0m' "$*"; }
ciano()   { printf '\033[36m%s\033[0m' "$*"; }
verde()   { printf '\033[32m%s\033[0m' "$*"; }
amarelo() { printf '\033[33m%s\033[0m' "$*"; }
erro()    { printf '\033[31merro:\033[0m %s\n' "$*" >&2; }

titulo() { printf '\n%s %s\n' "$(ciano '==>')" "$(negrito "$*")"; }
passo()  { printf '  %s %s\n' "$(verde '·')" "$*"; }
aviso()  { printf '  %s %s\n' "$(amarelo '!')" "$*"; }
nota()   { printf '  %s\n' "$*"; }

# Executa, ou apenas mostra o que executaria (--dry-run).
run() {
  if [ "$DRY" = "1" ]; then
    printf '  [dry-run] %s\n' "$*"
    return 0
  fi
  "$@"
}

# Pergunta sim/não lendo do terminal de verdade: o script costuma chegar por um
# pipe, e nesse caso o stdin é o próprio script, não o teclado.
perguntar() {
  local prompt="$1" padrao="${2:-s}" resposta
  if [ "$SIM" = "1" ] || [ ! -r /dev/tty ]; then
    [ "$padrao" = "s" ]
    return
  fi
  printf '  %s ' "$prompt"
  read -r resposta < /dev/tty || resposta=""
  resposta="${resposta:-$padrao}"
  case "$resposta" in
    s|S|sim|SIM|y|Y|yes) return 0 ;;
    *) return 1 ;;
  esac
}

ajuda() {
  cat <<'TXT'

transcritor — instalador

  curl -fsSL https://raw.githubusercontent.com/a32fred/transcritor/main/instalar.sh | bash

  --sim             não pergunta nada (baixa o modelo sugerido)
  --sem-modelo      não baixa modelo agora
  --modelo NOME     small | medium | large-v3-turbo | large-v3
  --sem-icone       não cria o ícone do menu nem o "Abrir com"
  --versao REF      ramo ou etiqueta do repositório (padrão: main)
  --dry-run         mostra o que faria, sem alterar nada
  -h, --help        esta ajuda

TXT
}

# ------------------------------------------------------------------ argumentos
while [ $# -gt 0 ]; do
  case "$1" in
    --sim)        SIM=1; BAIXAR_MODELO="sim"; shift ;;
    --sem-modelo) BAIXAR_MODELO="nao"; shift ;;
    --modelo)     MODELO="${2:?--modelo exige um nome}"; shift 2 ;;
    --sem-icone)  CRIAR_ICONE=0; shift ;;
    --versao)     VERSAO="${2:?--versao exige um ramo ou etiqueta}"; shift 2 ;;
    --dry-run)    DRY=1; shift ;;
    -h|--help)    ajuda; exit 0 ;;
    *) erro "opção desconhecida: $1"; exit 2 ;;
  esac
done

if [ "$DRY" != "1" ] && [ "$(id -u)" = "0" ]; then
  erro "não rode como root: o programa é instalado na sua pasta pessoal e o sudo é usado só quando precisa."
  exit 1
fi

printf '\n%s\n' "$(negrito 'transcritor') — transcrição de áudio e vídeo no seu computador, sem nuvem"
nota "$URL_SCRIPT"

# ------------------------------------------------------------- 1. dependências
titulo "1/5  conferindo o que já está instalado"

DISTRO="desconhecida"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  DISTRO="${ID:-${ID_LIKE:-desconhecida}}"
fi
passo "distribuição: $DISTRO"

GERENCIADOR=""; COMANDO_INSTALAR=()
case "$DISTRO" in
  fedora|rhel|centos|rocky|almalinux) GERENCIADOR="dnf";    COMANDO_INSTALAR=(dnf install -y) ;;
  debian|ubuntu|pop|linuxmint|zorin)   GERENCIADOR="apt";    COMANDO_INSTALAR=(apt-get install -y) ;;
  arch|manjaro|endeavouros)            GERENCIADOR="pacman"; COMANDO_INSTALAR=(pacman -S --needed --noconfirm) ;;
  opensuse*|suse*)                     GERENCIADOR="zypper"; COMANDO_INSTALAR=(zypper install -y) ;;
esac

instalar_pacotes() {
  local pacotes=("$@")
  [ "${#pacotes[@]}" -gt 0 ] || return 0

  if [ -z "$GERENCIADOR" ]; then
    aviso "não reconheci o gerenciador de pacotes desta distribuição."
    aviso "instale à mão e rode este script de novo: ${pacotes[*]}"
    return 1
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    aviso "sudo não está disponível; instale à mão: ${pacotes[*]}"
    return 1
  fi

  echo
  passo "vou instalar com ${negrito "$GERENCIADOR"}: ${pacotes[*]}"
  passo "isso pede a sua senha e usa a internet"
  if ! perguntar "posso instalar? [S/n]" s; then
    aviso "sem esses pacotes o transcritor não funciona."
    aviso "instale você mesmo e rode de novo: sudo $GERENCIADOR install ${pacotes[*]}"
    return 1
  fi

  if [ "$GERENCIADOR" = "apt" ]; then
    run sudo apt-get update || true
  fi
  run sudo "${COMANDO_INSTALAR[@]}" "${pacotes[@]}"
}

FALTANDO=()
command -v ffmpeg  >/dev/null 2>&1 || FALTANDO+=(ffmpeg)
command -v ffprobe >/dev/null 2>&1 || FALTANDO+=(ffmpeg)
command -v python3 >/dev/null 2>&1 || FALTANDO+=(python3)
command -v pipx    >/dev/null 2>&1 || FALTANDO+=(pipx)

# remove repetidos (ffmpeg aparece duas vezes quando ffmpeg e ffprobe faltam)
PACOTES=()
for p in "${FALTANDO[@]:-}"; do
  case " ${PACOTES[*]:-} " in *" $p "*) ;; *) PACOTES+=("$p") ;; esac
done

if [ "${#PACOTES[@]}" -eq 0 ]; then
  passo "ffmpeg, ffprobe, python3 e pipx: tudo no lugar"
else
  aviso "faltando: ${PACOTES[*]}"
  instalar_pacotes "${PACOTES[@]}" || true
fi

# No Fedora, o ffmpeg completo costuma vir do RPM Fusion; o ffmpeg-free, do
# repositório oficial, atende à maioria dos formatos e serve de plano B.
if ! command -v ffmpeg >/dev/null 2>&1 && [ "$DISTRO" = "fedora" ]; then
  aviso "tentando o pacote ffmpeg-free (repositório oficial do Fedora)"
  run sudo dnf install -y ffmpeg-free || true
fi

for exe in ffmpeg ffprobe; do
  if ! command -v "$exe" >/dev/null 2>&1; then
    erro "$exe não encontrado. Sem ele não dá para ler nem converter áudio."
    case "$DISTRO" in
      fedora|rhel)                 nota "sudo dnf install ffmpeg   (o ffmpeg completo exige o RPM Fusion)" ;;
      debian|ubuntu|pop|linuxmint) nota "sudo apt install ffmpeg" ;;
      arch|manjaro|endeavouros)    nota "sudo pacman -S ffmpeg" ;;
      *)                           nota "instale o ffmpeg pelo gerenciador de pacotes da sua distribuição" ;;
    esac
    exit 1
  fi
done
passo "ffmpeg e ffprobe prontos"

if ! command -v python3 >/dev/null 2>&1; then
  erro "python3 não encontrado. Instale o Python 3.10 ou superior e rode de novo."
  exit 1
fi

PY_OK="$(python3 - <<'PY' || true
import sys
print("sim" if sys.version_info >= (3, 10) else "%d.%d" % sys.version_info[:2])
PY
)"
if [ "$PY_OK" != "sim" ]; then
  erro "o python3 daqui é $PY_OK e o transcritor precisa de 3.10 ou superior."
  case "$DISTRO" in
    debian|ubuntu|pop|linuxmint) nota "sudo apt install python3.12" ;;
    fedora|rhel)                 nota "sudo dnf install python3.12" ;;
  esac
  exit 1
fi
passo "python3 $(python3 -c 'import platform; print(platform.python_version())')"

if ! command -v pipx >/dev/null 2>&1; then
  erro "pipx não encontrado. Instale (sudo dnf install pipx) e rode de novo."
  exit 1
fi

# ------------------------------------------------------- 2. instalar o programa
titulo "2/5  instalando o transcritor"

ESPEC="git+${REPO#git+}@${VERSAO}"
if [ "$DRY" != "1" ] && pipx list --short 2>/dev/null | grep -q '^transcritor '; then
  if [ "$VERSAO" = "main" ] && pipx upgrade transcritor >/dev/null 2>&1; then
    passo "já estava instalado e foi atualizado"
  else
    passo "já estava instalado — reinstalando a versão pedida"
    run pipx install --force "$ESPEC"
  fi
elif [ "$VERSAO" = "main" ] && { [ "$DRY" = "1" ] || pipx install transcritor >/dev/null 2>&1; }; then
  run pipx install transcritor
  passo "instalado do PyPI"
else
  nota "(se ainda não estiver no PyPI, a instalação vem direto do repositório)"
  run pipx install "$ESPEC"
fi

run pipx ensurepath >/dev/null 2>&1 || true

# O pipx pode estar configurado com outro diretório de binários (PIPX_BIN_DIR).
# Perguntar a ele evita que o comando fique "instalado mas fora do PATH".
PIPX_BIN="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null | tail -n 1 || true)"
case "$PIPX_BIN" in
  */bin|*/bin/) ;;
  *) PIPX_BIN="$HOME/.local/bin" ;;
esac
export PATH="$PIPX_BIN:$HOME/.local/bin:$PATH"

if [ "$DRY" != "1" ] && ! command -v transcrever >/dev/null 2>&1; then
  erro "instalou, mas o comando 'transcrever' não está no PATH."
  nota "abra um terminal novo, ou rode: export PATH=\"$PIPX_BIN:\$PATH\""
  exit 1
fi
passo "comando: $(command -v transcrever 2>/dev/null || echo "$PIPX_BIN/transcrever")"

# --------------------------------------------------- 3. integração com o menu
if [ "$CRIAR_ICONE" = "1" ]; then
  titulo "3/5  criando o ícone no menu do sistema"
  run transcrever desktop instalar || aviso "não consegui criar o ícone (é opcional; o resto funciona)"
else
  titulo "3/5  ícone do menu: pulado (--sem-icone)"
fi

# ----------------------------------------------------------------- 4. modelo
titulo "4/5  modelo de transcrição"

case "$BAIXAR_MODELO" in
  nao)
    passo "pulado (--sem-modelo)"
    nota "depois, rode: transcrever models download $MODELO"
    ;;
  sim)
    run transcrever models download "$MODELO" --usar \
      || aviso "o download falhou (internet?). Tente depois: transcrever models download $MODELO"
    ;;
  *)
    nota "O transcritor precisa de um modelo de inteligência artificial. Ele fica"
    nota "no seu computador e é baixado uma única vez:"
    echo
    nota "  small            ~484 MB   boa qualidade, o mais leve"
    nota "  medium           ~1,5 GB   muito boa"
    nota "  large-v3-turbo   ~1,6 GB   excelente e rápido  (sugerido)"
    nota "  large-v3         ~3,1 GB   a melhor qualidade, mais lento"
    echo
    if perguntar "baixar o modelo $MODELO agora? [S/n]" s; then
      run transcrever models download "$MODELO" --usar \
        || aviso "o download falhou (internet?). Tente depois: transcrever models download $MODELO"
    else
      BAIXAR_MODELO="nao"
      passo "sem problema: rode depois 'transcrever models download $MODELO'"
    fi
    ;;
esac

# -------------------------------------------------------------- 5. diagnóstico
titulo "5/5  conferindo a instalação"
if [ "$DRY" != "1" ]; then
  if [ "$BAIXAR_MODELO" = "nao" ]; then
    nota "o diagnóstico vai apontar o modelo em falta: é esperado, você pulou o download"
  fi
  transcrever doctor || aviso "o diagnóstico apontou algo — veja as linhas com ✘ acima"
fi

titulo "pronto"
cat <<'TXT'
  Como usar, sem decorar nada:

    · no menu do sistema, procure "transcritor" e clique: abre a interface,
      onde você arrasta os áudios e lê o resultado;

    · ou clique com o botão direito em um áudio e escolha
      "Abrir com → Transcrever com o transcritor";

    · no terminal, digite só:

          transcrever

      e responda o menu.

  Desinstalar:    pipx uninstall transcritor
  Atualizar:      pipx upgrade transcritor
  Seus arquivos:  ~/.local/share/transcritor   (transcrições, fila e modelos)
TXT
printf '\n'
