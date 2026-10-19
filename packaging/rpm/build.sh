#!/usr/bin/env bash
#
# Constrói o RPM do transcritor a partir do sdist (o mesmo arquivo publicado
# no PyPI), deixando o pacote pronto em dist/.
#
#   ./packaging/rpm/build.sh                 # deixa o .rpm em dist/
#   ./packaging/rpm/build.sh --outdir /tmp   # em outro diretório
#
# O rpmbuild roda aqui mesmo, com um diretório de trabalho temporário: nada é
# escrito no ~/rpmbuild do seu usuário.
set -euo pipefail

raiz=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
spec=$raiz/packaging/rpm/transcritor.spec
outdir=$raiz/dist
sdist=""

uso() {
    cat <<'FIM'
Constrói o RPM do transcritor.

Uso:
  ./packaging/rpm/build.sh [--outdir DIR] [--sdist ARQUIVO]

Por padrão o sdist é reconstruído a partir do código (dist/transcritor-<versão>
.tar.gz) antes de montar o pacote, para o RPM nunca sair de uma cópia velha.
Com --sdist, usa um arquivo específico — é o que a CI faz, para o pacote sair
exatamente do mesmo sdist que foi conferido e publicado.

Requer rpmbuild e python3-devel instalados.
FIM
}

while [ $# -gt 0 ]; do
    case $1 in
        --outdir) outdir=$2; shift 2 ;;
        --sdist) sdist=$2; shift 2 ;;
        -h|--help) uso; exit 0 ;;
        *) echo "opção desconhecida: $1" >&2; uso >&2; exit 2 ;;
    esac
done

versao=$(python3 - "$spec" <<'PY'
import pathlib, re, sys
texto = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
print(re.search(r"^Version:\s*(\S+)", texto, re.M).group(1))
PY
)
echo "== transcritor $versao: construindo o RPM =="

if [ -z "$sdist" ]; then
    sdist=$raiz/dist/transcritor-$versao.tar.gz
    echo "-- construindo o sdist $(basename "$sdist")"
    mkdir -p "$raiz/dist"
    rm -f "$sdist"
    if command -v uv >/dev/null 2>&1; then
        (cd "$raiz" && uv build --sdist --out-dir dist)
    else
        (cd "$raiz" && python3 -m build --sdist --outdir dist)
    fi
fi

if [ ! -f "$sdist" ]; then
    echo "sdist não encontrado: $sdist" >&2
    exit 1
fi

topdir=$(mktemp -d "${TMPDIR:-/tmp}/rpmbuild-transcritor.XXXXXX")
trap 'rm -rf "$topdir"' EXIT
mkdir -p "$topdir"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
cp "$sdist" "$topdir/SOURCES/"
cp "$spec" "$topdir/SPECS/"

echo "-- rpmbuild (as dependências vêm em wheels e são baixadas agora)"
rpmbuild -bb --define "_topdir $topdir" --define "_sourcedir $topdir/SOURCES" \
    "$topdir/SPECS/transcritor.spec"

mkdir -p "$outdir"
find "$topdir/RPMS" -name '*.rpm' -exec cp -v {} "$outdir/" \;
pacote=$(ls "$outdir"/transcritor-"$versao"-*.rpm | head -1)

echo
echo "pronto:  $pacote"
echo "tamanho: $(du -h "$pacote" | cut -f1)"
echo "conteúdo: rpm -qpl $pacote"
echo "extrair:  rpm2cpio $pacote | (cd /tmp/extraido && cpio -idm --quiet)"
