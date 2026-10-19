# Pacote RPM do transcritor, para Fedora e RHEL (pensado para o COPR).
#
# Construir localmente:   ./packaging/rpm/build.sh
# Instalar um já pronto:  sudo dnf install ./transcritor-1.1.1-1.fc44.x86_64.rpm
#
# Por que este pacote carrega as dependências:
#
# faster-whisper, ctranslate2 e PyAV não existem nos repositórios do Fedora
# (numpy, onnxruntime e tokenizers existem), então um pacote feito só de
# `Requires` não teria como ser instalado. Em vez disso, este pacote monta
# aqui o seu próprio ambiente virtual, a partir das wheels publicadas, e o
# guarda em %%{_libdir}/transcritor/venv — o Python do sistema não é tocado e
# não existe conflito de versões com outros pacotes.
#
# O resultado: `dnf install` funciona sem pipx e sem internet, o comando
# `transcrever` fica no sistema inteiro, e o único download que resta é o do
# modelo Whisper, na primeira transcrição (que é uma escolha sua, de 75 MB a
# 3 GB, feita pelo próprio programa).
#
# Este arquivo é usado também pelos builds por SCM (COPR), apontando para o
# sdist publicado no PyPI. Veja docs/pacotes-linux.md.

%global venv        %{_libdir}/%{name}/venv
%global licencas    %{_datadir}/licenses/%{name}

# As bibliotecas embutidas são wheels prontas de terceiros (ctranslate2,
# onnxruntime, PyAV...), não código compilado aqui. Extrair "debug info" delas
# não serve para ninguém e faz o build falhar: o find-debuginfo do Fedora exige
# uma nota de build ID que essas wheels não trazem. O strip continua acontecendo.
%global __debug_install_post %{nil}
# Mesma razão: sem build IDs, o rpmbuild não consegue criar os links de
# /usr/lib/debug/.build-id, e o pacote não sai.
%global _build_id_links none
# E não há "código-fonte de debug": não compilamos nada aqui. A macro precisa
# ser removida (não basta zerá-la), porque o guard do rpm testa se ela existe.
%undefine _debugsource_packages

# As bibliotecas dentro do ambiente são wheels prontas e autocontidas — e trazem
# os sonames "consertados" pelo auditwheel, que só existem aqui dentro. Sem
# filtrar, o rpm deduz dependências dessas bibliotecas e o `dnf install` passa a
# exigir do sistema coisas que estão no próprio pacote. Os arquivos seguem
# empacotados normalmente; só não entram na conta das dependências.
%global __requires_exclude_from .*%{_libdir}/%{name}/venv/.*
%global __provides_exclude_from .*%{_libdir}/%{name}/venv/.*

Name:           transcritor
Version:        1.1.1
Release:        1%{?dist}
Summary:        Transcrição local de áudio e vídeo em PT-BR, sem nuvem

License:        MIT AND BSD-3-Clause AND Apache-2.0 AND (Apache-2.0 OR BSD-2-Clause) AND 0BSD AND Zlib AND CC0-1.0 AND MPL-2.0 AND PSF-2.0 AND LGPL-2.1-or-later AND GPL-2.0-or-later
URL:            https://github.com/a32fred/transcritor
# O mesmo sdist publicado no PyPI (o formato canônico do índice).
Source0:        https://files.pythonhosted.org/packages/source/t/%{name}/%{name}-%{version}.tar.gz

BuildRequires:  python3-devel
BuildRequires:  python3-pip

# O ambiente embutido traz bibliotecas compiladas: elas valem para o ABI do
# Python com que foram instaladas aqui, e é isso que este Requires garante.
Requires:       python(abi) = %{python3_version}
# Os binários do ambiente precisam do glibc, que não entra na conta automática
# porque o ambiente interno foi filtrado acima (`python3` e `ffmpeg` já o
# trazem, mas deixar explícito evita depender dessa corrente).
Requires:       glibc
%if 0%{?fedora} || 0%{?rhel} >= 8
Requires:       /usr/bin/ffmpeg
Requires:       /usr/bin/ffprobe
%else
Requires:       ffmpeg
%endif

%description
O transcritor transforma áudio e vídeo em texto e legendas (txt, srt, vtt,
json e md) sem enviar nada para serviços externos: usa o faster-whisper sobre
o CTranslate2, roda na CPU e funciona offline depois de baixar o modelo.

Este pacote traz a ferramenta junto com o ambiente que ela usa, o comando
`transcrever` disponível para todos os usuários e a integração com a área de
trabalho: o ícone no menu e a opção "Abrir com o transcritor" nos arquivos de
áudio e vídeo.

%prep
%autosetup -n %{name}-%{version}

%build
# Nada a compilar: o código é Python puro e as dependências chegam em wheels.

%install
install -d %{buildroot}%{_bindir} %{buildroot}%{licencas}

# 1. O ambiente próprio, criado já no lugar definitivo: um ambiente virtual
#    não é realocável, então ele nasce dentro do buildroot, em %{buildroot}%{venv}.
%{python3} -m venv --without-pip %{buildroot}%{venv}
%{python3} -m pip --python %{buildroot}%{venv}/bin/python install \
    --no-cache-dir --no-warn-script-location --disable-pip-version-check .

# Bytecode e dados de teste não são necessários no pacote de runtime.
find %{buildroot}%{venv} -type d -name __pycache__ -prune -exec rm -rf {} +
find %{buildroot}%{venv} -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
rm -rf %{buildroot}%{venv}/lib*/python*/site-packages/numpy/random/tests
rm -f %{buildroot}%{venv}/lib*/python*/site-packages/numpy/_core/_operand_flag_tests*.so \
      %{buildroot}%{venv}/lib*/python*/site-packages/numpy/_core/_struct_ufunc_tests*.so \
      %{buildroot}%{venv}/lib*/python*/site-packages/numpy/_core/_multiarray_tests*.so \
      %{buildroot}%{venv}/lib*/python*/site-packages/numpy/_core/_rational_tests*.so \
      %{buildroot}%{venv}/lib*/python*/site-packages/numpy/_core/_simd*.so \
      %{buildroot}%{venv}/lib*/python*/site-packages/numpy/_core/_umath_tests*.so
rm -f %{buildroot}%{venv}/lib*/python*/site-packages/tqdm/completion.sh

# Wheels às vezes marcam .py e METADATA como executáveis; normaliza os modos.
find %{buildroot}%{venv} -type d -exec chmod 0755 {} +
find %{buildroot}%{venv} -type f -exec chmod 0644 {} +
find %{buildroot}%{venv} -type f -name '*.so*' -exec chmod 0755 {} +

# 2. O ambiente traz comandos das próprias dependências (ct2-*, f2py, hf) e
#    arquivos que só existem para o "venv" (activate, .gitignore). Nada disso é
#    do transcritor: o pacote fica só com o comando dele e com o intérprete.
for sobra in %{buildroot}%{venv}/bin/*; do
    case "$(basename "$sobra")" in
        python|python3|python3.*) ;;
        *) rm -f "$sobra" ;;
    esac
done
rm -f %{buildroot}%{venv}/.gitignore

# 3. Os caminhos que o pip gravou apontando para o diretório de construção
#    (o `#!` dos comandos e o `command =` do pyvenv.cfg) precisam apontar para
#    o lugar de verdade. Binários ficam de fora: só texto é reescrito.
grep -rlI --exclude-dir=__pycache__ %{buildroot} %{buildroot}%{venv} 2>/dev/null \
    | xargs -r sed -i "s|%{buildroot}||g"

# 4. Um wrapper do sistema chama o módulo com o Python privado; não depende do
#    shebang que o pip gerou enquanto o venv ainda estava no buildroot.
cat > %{buildroot}%{_bindir}/transcrever <<'EOF'
#!/bin/sh
exec %{venv}/bin/python -B -m transcritor "$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/transcrever
ln -s transcrever %{buildroot}%{_bindir}/transcritor

# 5. O ícone no menu e o "Abrir com", gerados pelo próprio transcritor: a
#    lista de tipos de arquivo e o comando do ícone vêm do programa, não de
#    uma cópia que envelhece dentro do empacotamento.
PYTHONDONTWRITEBYTECODE=1 %{buildroot}%{venv}/bin/python -B -m transcritor desktop instalar \
    --destino %{buildroot}%{_datadir} --exec %{_bindir}/transcrever
find %{buildroot}%{venv} -type d -name __pycache__ -prune -exec rm -rf {} +
find %{buildroot}%{venv} -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

# Manual em português, disponível por `man transcrever`.
install -Dpm0644 docs/transcrever.1 %{buildroot}%{_mandir}/man1/transcrever.1
ln -s transcrever.1 %{buildroot}%{_mandir}/man1/transcritor.1

# 6. As licenças do que vai embutido. É código de terceiros dentro do pacote,
#    então cada um mantém o seu texto aqui.
for info in %{buildroot}%{venv}/lib*/python*/site-packages/*.dist-info; do
    pacote=$(basename "$info" .dist-info)
    for origem in "$info"/licenses/* "$info"/LICENSE* "$info"/COPYING*; do
        [ -f "$origem" ] || continue
        cp -p "$origem" "%{buildroot}%{licencas}/$pacote-$(basename "$origem")"
    done
done

# O venv cria python3 como link absoluto para o intérprete do host. Depois de
# toda operação de build que precisa desse link, trocamos por um relativo ao
# /usr/bin/python3 de runtime: sem aviso de link absoluto no pacote.
ln -sfn ../../../../bin/python3 %{buildroot}%{venv}/bin/python3

%check
# Confere que o que foi empacotado abre e traz os dados da interface.
site=$(find %{buildroot}%{venv}/lib* -type d -name site-packages -print -quit)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$site" %{python3} -B -m transcritor --version
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$site" %{python3} -B -c "\
import pathlib, transcritor; \
pacote = pathlib.Path(transcritor.__file__).parent; \
assert (pacote / 'web/templates/index.html').exists(), 'falta o template da interface'; \
assert len(list((pacote / 'data').iterdir())) == 3, 'faltam os .desktop e o ícone'; \
print('dados do pacote presentes')"

%post
update-desktop-database -q %{_datadir}/applications &>/dev/null || :
touch --no-create %{_datadir}/icons/hicolor &>/dev/null || :
gtk-update-icon-cache -q -t %{_datadir}/icons/hicolor &>/dev/null || :
mandb -q &>/dev/null || :

%postun
update-desktop-database -q %{_datadir}/applications &>/dev/null || :
touch --no-create %{_datadir}/icons/hicolor &>/dev/null || :
mandb -q &>/dev/null || :

%files
%{_bindir}/transcrever
%{_bindir}/transcritor
%{_libdir}/%{name}
%{_datadir}/applications/transcritor.desktop
%{_datadir}/applications/transcritor-arquivo.desktop
%{_datadir}/icons/hicolor/scalable/apps/transcritor.svg
%{_mandir}/man1/transcrever.1*
%{_mandir}/man1/transcritor.1*
%{licencas}/*
%license LICENSE
%doc README.md CHANGELOG.md CONTRIBUTING.md docs/pacotes-linux.md

%changelog
* Sat Oct 17 2026 Frederico Carlos <fredericocarlos.a32@gmail.com> - 1.1.1-1
- Primeira versão empacotada: ambiente próprio, comando `transcrever`, ícone no
  menu e "Abrir com" para arquivos de áudio e vídeo.
