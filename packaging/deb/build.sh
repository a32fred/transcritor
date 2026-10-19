#!/usr/bin/env bash
# Constrói um .deb a partir do sdist, com dependências isoladas em um venv.
set -euo pipefail

raiz=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
outdir=$raiz/dist
sdist=""
python=${PYTHON:-python3}

usage() {
    cat <<'EOF'
Constrói um pacote .deb do transcritor.
Uso: ./packaging/deb/build.sh [--sdist ARQUIVO] [--outdir DIR] [--python EXEC]
Requer dpkg-deb, dpkg, tar e um Python com pip; precisa de internet para as wheels.
EOF
}

while [ $# -gt 0 ]; do
    case $1 in
        --sdist) sdist=$2; shift 2 ;;
        --outdir) outdir=$2; shift 2 ;;
        --python) python=$2; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "opção desconhecida: $1" >&2; usage >&2; exit 2 ;;
    esac
done

versao=$(python3 - "$raiz/pyproject.toml" <<'PY'
import pathlib, re, sys
texto = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r'^version\s*=\s*["\']([^"\']+)', texto, re.M)
if not match:
    raise SystemExit("version não encontrada em pyproject.toml")
print(match.group(1))
PY
)

if [ -z "$sdist" ]; then
    sdist=$raiz/dist/transcritor-$versao.tar.gz
    mkdir -p "$raiz/dist"
    rm -f "$sdist"
    if command -v uv >/dev/null 2>&1; then
        (cd "$raiz" && uv build --sdist --out-dir dist)
    else
        (cd "$raiz" && python3 -m build --sdist --outdir dist)
    fi
fi
test -f "$sdist" || { echo "sdist não encontrado: $sdist" >&2; exit 1; }
for comando in dpkg dpkg-deb tar strip; do
    command -v "$comando" >/dev/null || { echo "falta o comando: $comando" >&2; exit 1; }
done

pyver=$($python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
arquitetura=$(dpkg --print-architecture)
limite_python=$($python - "$pyver" <<'PY'
import sys
major, minor = map(int, sys.argv[1].split("."))
print(f"{major}.{minor + 1}")
PY
)
tmp=$(mktemp -d "${TMPDIR:-/tmp}/transcritor-deb.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

fonte=$tmp/fonte
root=$tmp/root
mkdir -p "$fonte" "$root/DEBIAN" "$root/usr/lib/transcritor" \
    "$root/usr/bin" "$root/usr/share/doc/transcritor/licenses"
tar -xzf "$sdist" -C "$fonte"
src=$fonte/transcritor-$versao
test -d "$src" || { echo "sdist sem o diretório transcritor-$versao" >&2; exit 1; }
install -m 0644 "$src/README.md" "$src/CONTRIBUTING.md" \
    "$root/usr/share/doc/transcritor/"
install -m 0644 "$src/CHANGELOG.md" "$root/usr/share/doc/transcritor/changelog"
gzip -9n "$root/usr/share/doc/transcritor/changelog"
install -D -m 0644 "$src/docs/pacotes-linux.md" \
    "$root/usr/share/doc/transcritor/pacotes-linux.md"
install -D -m 0644 "$src/docs/transcrever.1" \
    "$root/usr/share/man/man1/transcrever.1"
gzip -9n "$root/usr/share/man/man1/transcrever.1"
ln -s transcrever.1.gz "$root/usr/share/man/man1/transcritor.1.gz"

venv=$root/usr/lib/transcritor/venv
$python -m venv --without-pip "$venv"
# Pip do construtor instala no ambiente de destino; não empacotamos pip nem
# os comandos auxiliares das dependências.
$python -m pip --python "$venv/bin/python" install \
    --no-cache-dir --no-warn-script-location --disable-pip-version-check "$src"

# Bytecode e dados de teste não são necessários no pacote de runtime.
find "$venv" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$venv" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
rm -rf "$venv"/lib*/python*/site-packages/numpy/random/tests
rm -f "$venv"/lib*/python*/site-packages/numpy/_core/_operand_flag_tests*.so \
      "$venv"/lib*/python*/site-packages/numpy/_core/_struct_ufunc_tests*.so \
      "$venv"/lib*/python*/site-packages/numpy/_core/_multiarray_tests*.so \
      "$venv"/lib*/python*/site-packages/numpy/_core/_rational_tests*.so \
      "$venv"/lib*/python*/site-packages/numpy/_core/_simd*.so \
      "$venv"/lib*/python*/site-packages/numpy/_core/_umath_tests*.so
rm -f "$venv"/lib*/python*/site-packages/tqdm/completion.sh

# Normaliza os modos das wheels (algumas marcam .py/METADATA como executáveis).
find "$venv" -type d -exec chmod 0755 {} +
find "$venv" -type f -exec chmod 0644 {} +
find "$venv" -type f -name '*.so*' -exec strip --strip-unneeded {} +
# Debian policy keeps private shared libraries readable, not executable.
find "$venv" -type f -name '*.so*' -exec chmod 0644 {} +

# Corrige caminhos de buildroot em arquivos de texto, sem tocar nos binários.
grep -rlI --exclude-dir=__pycache__ "$root" "$venv" 2>/dev/null \
    | xargs -r sed -i "s|$root||g"
for sobra in "$venv"/bin/*; do
    case "$(basename "$sobra")" in
        python|python3|python3.*) ;;
        *) rm -f "$sobra" ;;
    esac
done
rm -f "$venv/.gitignore"

# Wrapper de sistema, com shebang comum e argumento preservado.
cat > "$root/usr/bin/transcrever" <<'EOF'
#!/bin/sh
exec /usr/lib/transcritor/venv/bin/python -B -m transcritor "$@"
EOF
chmod 0755 "$root/usr/bin/transcrever"
ln -s transcrever "$root/usr/bin/transcritor"

# Arquivos de menu e tipos MIME vêm da implementação do próprio transcritor.
PYTHONDONTWRITEBYTECODE=1 "$venv/bin/python" -B -m transcritor desktop instalar \
    --destino "$root/usr/share" --exec /usr/bin/transcrever

# Licenças de todas as wheels embutidas.
for info in "$venv"/lib*/python*/site-packages/*.dist-info; do
    [ -d "$info" ] || continue
    pacote=$(basename "$info" .dist-info)
    for origem in "$info"/licenses/* "$info"/LICENSE* "$info"/COPYING*; do
        [ -f "$origem" ] || continue
        cp -p "$origem" "$root/usr/share/doc/transcritor/licenses/$pacote-$(basename "$origem")"
    done
done

# Nenhum bytecode gerado durante as operações de empacotamento deve entrar.
find "$venv" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$venv" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

# Debian Policy pede o texto de copyright/licença neste caminho.
{
    cat <<'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: transcritor
Source: https://github.com/a32fred/transcritor

Files: usr/lib/transcritor/venv/lib/*/site-packages/*
Copyright: Various upstream authors
License: various
 Each bundled third-party Python package is distributed under its own license.
 Its license text is preserved in /usr/share/doc/transcritor/licenses/.

Files: usr/lib/transcritor/venv/lib/*/site-packages/transcritor/*
Copyright: 2024-2026 Frederico Carlos
License: MIT
EOF
    sed 's/^/ /' "$src/LICENSE"
    cat <<'EOF'

Files: usr/share/doc/transcritor/licenses/*
Copyright: Various upstream authors
License: various
 The license text for each bundled dependency is included in this directory.
EOF
} > "$root/usr/share/doc/transcritor/copyright"

cat > "$root/usr/share/doc/transcritor/changelog.Debian" <<EOF
transcritor ($versao-1) unstable; urgency=medium

  * Empacotamento inicial para Debian e Ubuntu.

 -- Frederico Carlos <fredericocarlos.a32@gmail.com>  $(date -R)
EOF
gzip -9n "$root/usr/share/doc/transcritor/changelog.Debian"

# Estas wheels trazem bibliotecas nativas privadas de propósito. As licenças
# disponíveis são mantidas em /usr/share/doc/transcritor/licenses/.
mkdir -p "$root/usr/share/lintian/overrides"
cat > "$root/usr/share/lintian/overrides/transcritor" <<'EOF'
# Bibliotecas nativas incorporadas pelas wheels oficiais das dependências.
transcritor: embedded-library *
# Lapack Lite and the ONNX quantization helper are private wheel internals.
transcritor: library-not-linked-against-libc
transcritor: unusual-interpreter
# Initial release of the Debian package, with no Debian bug number to close.
transcritor: initial-upload-closes-no-bugs
EOF
installed_kb=$(du -sk "$root" | cut -f1)

cat > "$root/DEBIAN/control" <<EOF
Package: transcritor
Version: $versao-1
Section: sound
Priority: optional
Architecture: $arquitetura
Installed-Size: $installed_kb
Maintainer: Frederico Carlos <fredericocarlos.a32@gmail.com>
Depends: python3 (>= $pyver), python3 (<< $limite_python), libc6 (>= 2.28), ffmpeg
Description: Transcrição local de áudio e vídeo em PT-BR
 Transcreve áudio e vídeo para texto e legendas sem enviar arquivos a serviços
 externos. O pacote traz um ambiente Python isolado; o modelo é baixado pelo
 usuário na primeira transcrição e permanece local.
EOF

cat > "$root/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
    fi
    if command -v mandb >/dev/null 2>&1; then
        mandb -q || true
    fi
fi
exit 0
EOF
cat > "$root/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
fi
if command -v mandb >/dev/null 2>&1; then
    mandb -q || true
fi
exit 0
EOF
chmod 0755 "$root/DEBIAN/postinst" "$root/DEBIAN/postrm"

# Smokes antes de gravar o artefato.
PYTHONDONTWRITEBYTECODE=1 "$venv/bin/python" -B -m transcritor --version
test -s "$root/usr/share/applications/transcritor.desktop"
test -s "$root/usr/share/applications/transcritor-arquivo.desktop"
test -s "$root/usr/share/icons/hicolor/scalable/apps/transcritor.svg"
# Torna python3 relativo só depois de todas as verificações que executam o venv
# no build host; em runtime, o alvo resolve para /usr/bin/python3.
ln -sfn ../../../../bin/python3 "$venv/bin/python3"

mkdir -p "$outdir"
pacote=$outdir/transcritor_${versao}-1_${arquitetura}.deb
dpkg-deb --build --root-owner-group "$root" "$pacote"
dpkg-deb --info "$pacote"
echo "pronto: $pacote ($(du -h "$pacote" | cut -f1))"
